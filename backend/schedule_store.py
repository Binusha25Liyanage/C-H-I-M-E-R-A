"""
schedule_store.py
Scheduled jobs and the holiday calendar.

A *job* is "do this kind of work, at these times, on these days":

  frequency   daily    every day at each time
              weekly   on the chosen weekdays (Mon..Sun) at each time
              monthly  on the chosen days of the month (1-31, or 'last');
                       a day the month doesn't have (e.g. 31 in November)
                       runs on that month's last day instead
              once     on one date

Every job also obeys the global controls in automation_settings (Pause,
Skip today) and, unless switched off per job, skips the dates in the
holiday calendar.

Job types: only the ones whose engine exists can be saved. The others are
listed so the screen can show what's coming, but they can't be scheduled
yet.

Every scheduled firing is written to schedule_fires before the work starts,
so a slot never runs twice — whether the in-app scheduler or Windows Task
Scheduler noticed it first.
"""

import calendar
import json
import re
from datetime import date, datetime, timedelta

import db

FREQUENCIES = ("daily", "weekly", "monthly", "once")
JOB_TYPES = {
    "sfa_downloads": {"label": "SFA report downloads", "available": True,
                      "hint": "Logs in to SFA, downloads the report profiles, imports them and builds the reports."},
    "master_sync": {"label": "Master data sync", "available": False,
                    "hint": "Itinerary, Territories and Outlet Master from SFA (coming next)."},
    "visit_state": {"label": "Visit state update", "available": False, "hint": "Coming soon."},
    "sequence": {"label": "Sequence update", "available": False, "hint": "Coming soon."},
    "loadings": {"label": "Distributor loadings (prepare)", "available": True,
                 "hint": "Reads the pending loading of every Daily distributor and asks for your approval — "
                         "it never confirms a loading on its own."},
}
REPORT_DAYS = ("today", "yesterday")
# Each job type has an on/off switch in automation_settings (Schedules > Automations).
TYPE_SWITCH = {"sfa_downloads": "auto_sfa_downloads", "master_sync": "auto_master_sync",
               "visit_state": "auto_visit_state", "sequence": "auto_sequence", "loadings": "auto_loadings"}


def type_on(job, settings):
    return settings.get(TYPE_SWITCH.get(job["job_type"], ""), "1") != "0"
LAST_DAY = "last"
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def init_schema():
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedule_jobs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            job_type      TEXT NOT NULL,
            frequency     TEXT NOT NULL,          -- daily | weekly | monthly | once
            times         TEXT NOT NULL,          -- 'HH:MM,HH:MM'
            weekdays      TEXT NOT NULL DEFAULT '1111111',
            month_days    TEXT NOT NULL DEFAULT '1',   -- '1,15,last'
            once_date     TEXT NOT NULL DEFAULT '',
            options       TEXT NOT NULL DEFAULT '{}',
            enabled       INTEGER NOT NULL DEFAULT 1,
            skip_holidays INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT,
            updated_at    TEXT
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS holidays (
            day  TEXT PRIMARY KEY,                -- 'YYYY-MM-DD'
            name TEXT NOT NULL DEFAULT ''
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedule_fires (
            job_id   INTEGER NOT NULL,
            day      TEXT NOT NULL,
            slot     TEXT NOT NULL,
            fired_at TEXT NOT NULL,
            trigger  TEXT NOT NULL,
            outcome  TEXT,
            PRIMARY KEY (job_id, day, slot)
        )""")
    conn.commit()
    migrated = cur.execute("SELECT value FROM automation_settings WHERE key='schedule_migrated'").fetchone()
    has_jobs = cur.execute("SELECT COUNT(*) FROM schedule_jobs").fetchone()[0]
    conn.close()
    if not migrated:
        if not has_jobs:
            _migrate_legacy_schedule()
        conn = db.get_connection()
        conn.execute("INSERT OR REPLACE INTO automation_settings (key, value) VALUES ('schedule_migrated', '1')")
        conn.commit()
        conn.close()


def _migrate_legacy_schedule():
    """The first version kept one schedule (run times + weekdays) in
    automation_settings. Turn it into the first job so nothing changes for
    the user."""
    import automation_store as AS
    s = AS.get_automation_settings()
    times = s.get("run_times") or "10:00"
    weekdays = s.get("weekdays") or "1111110"
    save_job({"name": "Daily SFA downloads", "job_type": "sfa_downloads",
              "frequency": "daily" if weekdays == "1111111" else "weekly",
              "times": times, "weekdays": weekdays, "options": {"report_day": "today", "profile_ids": []},
              "enabled": True, "skip_holidays": True})


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
_COLS = ["id", "name", "job_type", "frequency", "times", "weekdays", "month_days", "once_date", "options",
         "enabled", "skip_holidays", "created_at", "updated_at"]


def _row(r):
    j = dict(zip(_COLS, r))
    j["times_list"] = [t for t in j["times"].split(",") if t]
    j["month_days_list"] = [d for d in j["month_days"].split(",") if d]
    try:
        j["options"] = json.loads(j["options"] or "{}")
    except ValueError:
        j["options"] = {}
    j["enabled"] = bool(j["enabled"])
    j["skip_holidays"] = bool(j["skip_holidays"])
    j["type_label"] = JOB_TYPES.get(j["job_type"], {}).get("label", j["job_type"])
    j["summary"] = describe(j)
    return j


def list_jobs():
    conn = db.get_connection()
    rows = conn.execute(f"SELECT {', '.join(_COLS)} FROM schedule_jobs ORDER BY id").fetchall()
    conn.close()
    return [_row(r) for r in rows]


def get_job(job_id):
    conn = db.get_connection()
    r = conn.execute(f"SELECT {', '.join(_COLS)} FROM schedule_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return _row(r) if r else None


def _clean_times(v):
    items = v if isinstance(v, list) else [x for x in str(v or "").split(",") if x.strip()]
    out = set()
    for t in items:
        m = _TIME.match(str(t).strip())
        if not m or not (0 <= int(m[1]) < 24 and 0 <= int(m[2]) < 60):
            raise ValueError(f"'{t}' is not a valid time (use HH:MM, 24-hour).")
        out.add(f"{int(m[1]):02d}:{m[2]}")
    if not out:
        raise ValueError("Add at least one run time.")
    return ",".join(sorted(out))


def _clean_month_days(v):
    items = v if isinstance(v, list) else [x for x in str(v or "").split(",") if x.strip()]
    out = set()
    for d in items:
        d = str(d).strip().lower()
        if d == LAST_DAY:
            out.add(LAST_DAY)
        elif d.isdigit() and 1 <= int(d) <= 31:
            out.add(str(int(d)))
        else:
            raise ValueError(f"'{d}' is not a day of the month (1-31 or 'last').")
    if not out:
        raise ValueError("Choose at least one day of the month.")
    return ",".join(sorted(out, key=lambda x: 99 if x == LAST_DAY else int(x)))


def _clean_job(j):
    name = str(j.get("name") or "").strip()
    if not name:
        raise ValueError("Give the job a name.")
    jt = j.get("job_type") or "sfa_downloads"
    if jt not in JOB_TYPES:
        raise ValueError(f"Unknown job type '{jt}'.")
    if not JOB_TYPES[jt]["available"]:
        raise ValueError(f"{JOB_TYPES[jt]['label']} can't be scheduled yet — that feature isn't built.")
    freq = j.get("frequency") or "daily"
    if freq not in FREQUENCIES:
        raise ValueError(f"Unknown frequency '{freq}'.")
    out = {"name": name, "job_type": jt, "frequency": freq, "times": _clean_times(j.get("times")),
           "weekdays": "1111111", "month_days": "1", "once_date": "",
           "enabled": 1 if j.get("enabled", True) else 0,
           "skip_holidays": 1 if j.get("skip_holidays", True) else 0}
    if freq == "weekly":
        wd = str(j.get("weekdays") or "")
        if not re.match(r"^[01]{7}$", wd):
            raise ValueError("Weekdays must be 7 on/off values.")
        if "1" not in wd:
            raise ValueError("Choose at least one weekday.")
        out["weekdays"] = wd
    elif freq == "monthly":
        out["month_days"] = _clean_month_days(j.get("month_days"))
    elif freq == "once":
        od = str(j.get("once_date") or "")
        try:
            od_d = date.fromisoformat(od)
        except ValueError:
            raise ValueError("Choose the date for a one-time run.")
        last_t = max(out["times"].split(","))
        if datetime.combine(od_d, datetime.strptime(last_t, "%H:%M").time()) < datetime.now() and j.get("enabled", True):
            raise ValueError("That date and time have already passed.")
        out["once_date"] = od
    opts = dict(j.get("options") or {})
    if jt == "sfa_downloads":
        rd = opts.get("report_day") or "today"
        if rd not in REPORT_DAYS:
            raise ValueError("Report date must be today or yesterday.")
        ids = [int(x) for x in (opts.get("profile_ids") or [])]
        if ids:
            import automation_store as AS
            known = {p["id"] for p in AS.list_profiles()}
            missing = [i for i in ids if i not in known]
            if missing:
                raise ValueError("Some selected report profiles no longer exist — reopen the job and choose again.")
        opts = {"report_day": rd, "profile_ids": ids}
    out["options"] = json.dumps(opts)
    return out


def save_job(j):
    """Insert (no id) or update. Returns (ok, message, job)."""
    try:
        c = _clean_job(j)
    except ValueError as e:
        return False, str(e), None
    now = datetime.now().isoformat(timespec="seconds")
    conn = db.get_connection()
    if j.get("id"):
        cols = ", ".join(f"{k}=?" for k in c)
        cur = conn.execute(f"UPDATE schedule_jobs SET {cols}, updated_at=? WHERE id=?",
                           (*c.values(), now, int(j["id"])))
        if cur.rowcount == 0:
            conn.close()
            return False, "That job no longer exists.", None
        jid = int(j["id"])
    else:
        cur = conn.execute(f"INSERT INTO schedule_jobs ({', '.join(c)}, created_at, updated_at) "
                           f"VALUES ({', '.join('?' * len(c))}, ?, ?)", (*c.values(), now, now))
        jid = cur.lastrowid
    conn.commit()
    conn.close()
    return True, "Saved.", get_job(jid)


def set_job_enabled(job_id, enabled):
    conn = db.get_connection()
    conn.execute("UPDATE schedule_jobs SET enabled=?, updated_at=? WHERE id=?",
                 (1 if enabled else 0, datetime.now().isoformat(timespec="seconds"), job_id))
    conn.commit()
    conn.close()


def delete_job(job_id):
    conn = db.get_connection()
    conn.execute("DELETE FROM schedule_jobs WHERE id=?", (job_id,))
    conn.execute("DELETE FROM schedule_fires WHERE job_id=?", (job_id,))
    conn.commit()
    conn.close()


_DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def describe(j):
    times = ", ".join(j["times_list"]) if "times_list" in j else j["times"].replace(",", ", ")
    f = j["frequency"]
    if f == "daily":
        when = "Every day"
    elif f == "weekly":
        days = [d for d, on in zip(_DAY_ABBR, j["weekdays"]) if on == "1"]
        when = "Mon–Sat" if j["weekdays"] == "1111110" else "Mon–Fri" if j["weekdays"] == "1111100" else ", ".join(days)
    elif f == "monthly":
        parts = [("last day" if d == LAST_DAY else _ordinal(int(d))) for d in j["month_days"].split(",") if d]
        when = "Monthly on the " + ", ".join(parts)
    else:
        try:
            when = "Once on " + date.fromisoformat(j["once_date"]).strftime("%a %d %b %Y")
        except ValueError:
            when = "Once"
    return f"{when} at {times}"


# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------
def list_holidays(year=None):
    conn = db.get_connection()
    if year:
        rows = conn.execute("SELECT day, name FROM holidays WHERE substr(day, 1, 4)=? ORDER BY day",
                            (str(year),)).fetchall()
    else:
        rows = conn.execute("SELECT day, name FROM holidays ORDER BY day").fetchall()
    conn.close()
    return [{"day": d, "name": n} for d, n in rows]


def holiday_set():
    conn = db.get_connection()
    s = {r[0] for r in conn.execute("SELECT day FROM holidays")}
    conn.close()
    return s


def add_holidays(text_or_items):
    """
    Accepts a list of {"day", "name"} or pasted text, one per line:
        2026-11-24  Il Full Moon Poya Day
        24/11/2026, Poya
    Returns (added_count, problems).
    """
    items, problems = [], []
    if isinstance(text_or_items, str):
        for n, line in enumerate(text_or_items.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4})[\s,;\t]*(.*)$", line)
            if not m:
                problems.append(f"Line {n}: no date found in “{line[:40]}”.")
                continue
            items.append({"day": m[1], "name": m[2]})
    else:
        items = list(text_or_items or [])
    rows = []
    for it in items:
        raw = str(it.get("day") or "").strip()
        try:
            if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
                d = date.fromisoformat(raw)
            else:
                dd, mm, yy = re.split(r"[/.-]", raw)
                d = date(int(yy), int(mm), int(dd))
        except (ValueError, TypeError):
            problems.append(f"“{raw}” is not a valid date (use YYYY-MM-DD or DD/MM/YYYY).")
            continue
        rows.append((d.isoformat(), str(it.get("name") or "").strip()[:80] or "Holiday"))
    if rows:
        conn = db.get_connection()
        conn.executemany("INSERT OR REPLACE INTO holidays (day, name) VALUES (?, ?)", rows)
        conn.commit()
        conn.close()
    return len(rows), problems


def delete_holiday(day):
    conn = db.get_connection()
    conn.execute("DELETE FROM holidays WHERE day=?", (day,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Rules: when does a job run?
# ---------------------------------------------------------------------------
def runs_on(job, d, holidays=None):
    """Does this job run on date d (ignoring Pause / Skip today, which are global)?"""
    if not job["enabled"]:
        return False
    if job["skip_holidays"] and d.isoformat() in (holidays if holidays is not None else holiday_set()):
        return False
    f = job["frequency"]
    if f == "daily":
        return True
    if f == "weekly":
        return job["weekdays"][d.weekday()] == "1"
    if f == "monthly":
        last = calendar.monthrange(d.year, d.month)[1]
        for md in job["month_days"].split(","):
            if md == LAST_DAY and d.day == last:
                return True
            if md.isdigit() and min(int(md), last) == d.day:
                return True
        return False
    if f == "once":
        return job["once_date"] == d.isoformat()
    return False


def holiday_name(d):
    conn = db.get_connection()
    r = conn.execute("SELECT name FROM holidays WHERE day=?", (d.isoformat(),)).fetchone()
    conn.close()
    return r[0] if r else None


def occurrences(start, end, jobs=None, holidays=None, include_skipped=False):
    """Every (job, datetime) between start and end (datetimes), in time order.
    With include_skipped, holiday occurrences are returned too, flagged."""
    jobs = jobs if jobs is not None else list_jobs()
    hol = holidays if holidays is not None else holiday_set()
    out = []
    d = start.date()
    while d <= end.date():
        for j in jobs:
            if not j["enabled"]:
                continue
            on = runs_on(j, d, hol)
            skipped_for_holiday = False
            if not on and include_skipped and j["skip_holidays"] and d.isoformat() in hol:
                on = runs_on(dict(j, skip_holidays=False), d, hol)
                skipped_for_holiday = on
            if not on:
                continue
            for t in j["times_list"]:
                h, m = (int(x) for x in t.split(":"))
                dt = datetime(d.year, d.month, d.day, h, m)
                if start <= dt <= end:
                    out.append({"job": j, "at": dt, "slot": t, "holiday": skipped_for_holiday})
        d += timedelta(days=1)
    out.sort(key=lambda o: (o["at"], o["job"]["id"]))
    return out


# ---------------------------------------------------------------------------
# Firing record (never run a slot twice)
# ---------------------------------------------------------------------------
def already_fired(job_id, day, slot):
    conn = db.get_connection()
    n = conn.execute("SELECT COUNT(*) FROM schedule_fires WHERE job_id=? AND day=? AND slot=?",
                     (job_id, day, slot)).fetchone()[0]
    conn.close()
    return n > 0


def record_fire(job_id, day, slot, trigger):
    """Claims the slot. Returns False if someone else already claimed it."""
    conn = db.get_connection()
    try:
        conn.execute("INSERT INTO schedule_fires (job_id, day, slot, fired_at, trigger) VALUES (?, ?, ?, ?, ?)",
                     (job_id, day, slot, datetime.now().isoformat(timespec="seconds"), trigger))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def set_fire_outcome(job_id, day, slot, outcome):
    conn = db.get_connection()
    conn.execute("UPDATE schedule_fires SET outcome=? WHERE job_id=? AND day=? AND slot=?",
                 (str(outcome)[:200], job_id, day, slot))
    conn.commit()
    conn.close()


def last_fires(limit=50):
    conn = db.get_connection()
    rows = conn.execute("""SELECT job_id, day, slot, fired_at, trigger, outcome FROM schedule_fires
                           ORDER BY fired_at DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    return [dict(zip(["job_id", "day", "slot", "fired_at", "trigger", "outcome"], r)) for r in rows]
