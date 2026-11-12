"""
outlet_store.py
Outlet Updates (sequence numbers / visit states changed by reps through a
Google Sheet per territory). This file holds the parts that don't touch the
browser:

  * working territories — which SFA territories Chimera looks after, with the
    rep, WhatsApp group and sheet for each (one rep per territory);
  * the monthly SFA itinerary of each working territory, day by day
    (badge WD / H and the route), as read by itinerary_automation.py;
  * which day Chimera is preparing: from the prepare time (default 23:59)
    it is the NEXT day; before it, today. If the PC was off at 23:59 the
    work is simply still due, so it happens the next time Chimera runs.

A day's state on the Today's Routes board:
  route      a working day with a route
  holiday    'H' in the SFA calendar — no route, no sheet
  no_route   a working day showing '-' (no route set)
  missing    SFA has no itinerary for that month yet
  failed     the last read of that month failed (reason in message)
  not_read   Chimera hasn't read that month yet
"""

import json
from datetime import date, datetime, timedelta

import db

DEFAULT_ITINERARY_URL = "https://lakmee.salespro.lk/it/month-itinerary"
DEFAULT_OUTLETS_URL = "https://lakmee.salespro.lk/it/level-eight"
DEFAULT_TERRITORY_URL = "https://lakmee.salespro.lk/it/level-six"     # Sales Organization > Territory > View Territory
READER_VERSION = 2   # bump to re-read every itinerary after a reader fix
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER",
          "NOVEMBER", "DECEMBER"]


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_schema():
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ou_territories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            territory   TEXT NOT NULL UNIQUE,      -- exactly as in SFA's Territory list
            enabled     INTEGER NOT NULL DEFAULT 0,
            rep         TEXT NOT NULL DEFAULT '',  -- from the itinerary (one rep per territory)
            distributor TEXT NOT NULL DEFAULT '',
            manager     TEXT NOT NULL DEFAULT '',
            wa_chat     TEXT NOT NULL DEFAULT '',  -- WhatsApp group for the sheet link (later step)
            sheet_url   TEXT NOT NULL DEFAULT '',  -- Google Sheet (later step)
            notes       TEXT NOT NULL DEFAULT '',
            seen_in_sfa INTEGER NOT NULL DEFAULT 1,
            updated_at  TEXT NOT NULL
        )""")
    have = {r[1] for r in cur.execute("PRAGMA table_info(ou_territories)")}
    for col in ("code", "region", "area", "supervisor_area", "sfa_status"):   # from SFA's View Territory page
        if col not in have:
            cur.execute(f"ALTER TABLE ou_territories ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ou_itinerary_months (
            territory TEXT NOT NULL,
            month     TEXT NOT NULL,               -- YYYY-MM
            status    TEXT NOT NULL,               -- ok | missing | failed
            read_at   TEXT NOT NULL,
            rep       TEXT NOT NULL DEFAULT '',
            distributor TEXT NOT NULL DEFAULT '',
            sfa_url   TEXT NOT NULL DEFAULT '',
            message   TEXT NOT NULL DEFAULT '',
            tried_at  TEXT NOT NULL DEFAULT '',  -- last attempt (successful or not)
            PRIMARY KEY (territory, month)
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ou_itinerary_days (
            territory TEXT NOT NULL,
            day       TEXT NOT NULL,               -- YYYY-MM-DD
            badge     TEXT NOT NULL DEFAULT '',    -- WD | H (as shown in SFA)
            route     TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (territory, day)
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ou_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL,             -- itinerary | detect
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            trigger     TEXT NOT NULL DEFAULT 'manual',
            target_day  TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'running',
            message     TEXT NOT NULL DEFAULT '',
            log         TEXT NOT NULL DEFAULT ''
        )""")
    cur.execute("CREATE TABLE IF NOT EXISTS ou_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    ver = cur.execute("SELECT value FROM ou_meta WHERE key='reader_version'").fetchone()
    if not ver or int(ver[0]) < READER_VERSION:
        # Itineraries read by the first version could belong to another territory (parallel tabs shared
        # SFA's search filters). Throw them away once; they're read again automatically.
        cur.execute("DELETE FROM ou_itinerary_days")
        cur.execute("DELETE FROM ou_itinerary_months")
        cur.execute("UPDATE ou_territories SET rep='', distributor=''")
        cur.execute("INSERT OR REPLACE INTO ou_meta (key, value) VALUES ('reader_version', ?)", (str(READER_VERSION),))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Working territories
# ---------------------------------------------------------------------------
_TCOLS = ["id", "territory", "enabled", "rep", "distributor", "manager", "wa_chat", "sheet_url", "notes",
          "seen_in_sfa", "updated_at", "code", "region", "area", "supervisor_area", "sfa_status"]


def _trow(r):
    t = dict(zip(_TCOLS, r))
    t["enabled"] = bool(t["enabled"])
    t["seen_in_sfa"] = bool(t["seen_in_sfa"])
    return t


def list_territories(enabled_only=False):
    conn = db.get_connection()
    rows = conn.execute(f"SELECT {', '.join(_TCOLS)} FROM ou_territories "
                        f"{'WHERE enabled=1 ' if enabled_only else ''}ORDER BY enabled DESC, territory").fetchall()
    conn.close()
    return [_trow(r) for r in rows]


def get_territory(name):
    conn = db.get_connection()
    r = conn.execute(f"SELECT {', '.join(_TCOLS)} FROM ou_territories WHERE territory=?", (name,)).fetchone()
    conn.close()
    return _trow(r) if r else None


_DETAIL = ("code", "region", "area", "supervisor_area", "sfa_status")


def merge_detected(items):
    """Territories read from SFA — names, or dicts {territory, code, region, area, supervisor_area, sfa_status}
    from the View Territory page. New ones are added switched off; known ones keep their choices (details refreshed)."""
    rows = []
    for it in items:
        d = it if isinstance(it, dict) else {"territory": it}
        name = str(d.get("territory") or "").strip()
        if name and name.lower() not in ("no name", "-") and name not in {r["territory"] for r in rows}:
            rows.append({"territory": name, **{k: str(d.get(k) or "").strip() for k in _DETAIL}})
    names = [r["territory"] for r in rows]
    conn = db.get_connection()
    known = {r[0] for r in conn.execute("SELECT territory FROM ou_territories")}
    added = [n for n in names if n not in known]
    conn.executemany("INSERT INTO ou_territories (territory, enabled, updated_at) VALUES (?, 0, ?)",
                     [(n, _now()) for n in added])
    for r in rows:
        vals = {k: r[k] for k in _DETAIL if r[k]}
        if vals:
            conn.execute(f"UPDATE ou_territories SET {', '.join(f'{k}=?' for k in vals)} WHERE territory=?",
                         [*vals.values(), r["territory"]])
    if names:
        conn.execute(f"UPDATE ou_territories SET seen_in_sfa = (territory IN ({', '.join('?' * len(names))}))", names)
    conn.commit()
    conn.close()
    return added


def add_territory(name):
    name = str(name or "").strip()
    if not name:
        return False, "Type the territory exactly as SFA shows it."
    if get_territory(name):
        return False, f"{name} is already in the list."
    conn = db.get_connection()
    conn.execute("INSERT INTO ou_territories (territory, enabled, updated_at) VALUES (?, 1, ?)", (name, _now()))
    conn.commit()
    conn.close()
    return True, f"{name} added and switched on."


def set_enabled(names, on):
    conn = db.get_connection()
    conn.executemany("UPDATE ou_territories SET enabled=?, updated_at=? WHERE territory=?",
                     [(1 if on else 0, _now(), n) for n in names])
    conn.commit()
    conn.close()


def update_territory(name, values):
    allowed = {"manager", "wa_chat", "sheet_url", "notes"}
    vals = {k: str(v or "").strip()[:500] for k, v in (values or {}).items() if k in allowed}
    if vals.get("sheet_url") and not vals["sheet_url"].startswith("https://docs.google.com/spreadsheets/"):
        return False, "The sheet link must be a Google Sheets link (https://docs.google.com/spreadsheets/…)."
    if not vals:
        return True, "Nothing to change."
    conn = db.get_connection()
    n = conn.execute(f"UPDATE ou_territories SET {', '.join(f'{k}=?' for k in vals)}, updated_at=? WHERE territory=?",
                     [*vals.values(), _now(), name]).rowcount
    conn.commit()
    conn.close()
    return (True, "Saved.") if n else (False, "That territory isn't in the list.")


def remove_territory(name):
    conn = db.get_connection()
    conn.execute("DELETE FROM ou_territories WHERE territory=?", (name,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Which day is being prepared
# ---------------------------------------------------------------------------
def _hm(text):
    h, m = (str(text or "23:59").split(":") + ["0"])[:2]
    return int(h), int(m)


def target_day(now=None, prepare_time="23:59"):
    """From the prepare time onwards Chimera works on tomorrow; before it, on today."""
    now = now or datetime.now()
    h, m = _hm(prepare_time)
    return now.date() + timedelta(days=1) if (now.hour, now.minute) >= (h, m) else now.date()


def prepare_boundary(now=None, prepare_time="23:59"):
    """The most recent prepare moment. A month read before it is read again (SFA may have been edited)."""
    now = now or datetime.now()
    h, m = _hm(prepare_time)
    today_at = datetime.combine(now.date(), datetime.min.time()).replace(hour=h, minute=m)
    return today_at if now >= today_at else today_at - timedelta(days=1)


def month_key(d):
    return f"{d.year:04d}-{d.month:02d}"


RETRY_FAILED_MIN = 15     # a failed read is tried again after this long
RETRY_MISSING_MIN = 60    # "no itinerary in SFA yet" is checked again after this long


def due_territories(now=None, prepare_time="23:59"):
    """Working territories whose itinerary for the day being prepared still needs reading:
    never read, read before the last prepare moment, or last attempt failed / month missing (retried later)."""
    now = now or datetime.now()
    day = target_day(now, prepare_time)
    boundary = prepare_boundary(now, prepare_time).isoformat(timespec="seconds")
    mk = month_key(day)
    conn = db.get_connection()
    reads = {r[0]: r[1:] for r in conn.execute(
        "SELECT territory, status, read_at, tried_at, message FROM ou_itinerary_months WHERE month=?", (mk,))}
    conn.close()
    failed_before = (now - timedelta(minutes=RETRY_FAILED_MIN)).isoformat(timespec="seconds")
    missing_before = (now - timedelta(minutes=RETRY_MISSING_MIN)).isoformat(timespec="seconds")
    due = []
    for t in list_territories(enabled_only=True):
        r = reads.get(t["territory"])
        if not r:
            due.append(t["territory"])
            continue
        status, read_at, tried_at, msg = r
        tried_at = tried_at or read_at
        if status == "failed" or msg.startswith("Last read failed"):
            if tried_at < failed_before:
                due.append(t["territory"])
        elif read_at < boundary:
            due.append(t["territory"])
        elif status == "missing" and tried_at < missing_before:
            due.append(t["territory"])
    return day, due


# ---------------------------------------------------------------------------
# Itinerary storage
# ---------------------------------------------------------------------------
def save_month(territory, month, status, days=None, rep="", distributor="", sfa_url="", message="", clear=False):
    """Stores one month's read. days = {'YYYY-MM-DD': (badge, route)}; a successful read replaces the month.
    clear=True: a failed read that also throws away the stored calendar (it can't be trusted)."""
    conn = db.get_connection()
    if clear:
        conn.execute("DELETE FROM ou_itinerary_days WHERE territory=? AND substr(day, 1, 7)=?", (territory, month))
        conn.execute("DELETE FROM ou_itinerary_months WHERE territory=? AND month=?", (territory, month))
        conn.execute("UPDATE ou_territories SET rep='', distributor='' WHERE territory=?", (territory,))
    if status == "ok":
        conn.execute("DELETE FROM ou_itinerary_days WHERE territory=? AND substr(day, 1, 7)=?", (territory, month))
        conn.executemany("INSERT INTO ou_itinerary_days (territory, day, badge, route) VALUES (?, ?, ?, ?)",
                         [(territory, d, b, r) for d, (b, r) in sorted((days or {}).items())])
    elif status == "missing":
        conn.execute("DELETE FROM ou_itinerary_days WHERE territory=? AND substr(day, 1, 7)=?", (territory, month))
    prev = conn.execute("SELECT status FROM ou_itinerary_months WHERE territory=? AND month=?",
                        (territory, month)).fetchone()
    if status == "failed" and prev and prev[0] == "ok":
        # keep the last good calendar; just note the failed attempt
        conn.execute("UPDATE ou_itinerary_months SET message=?, tried_at=? WHERE territory=? AND month=?",
                     (f"Last read failed: {message}", _now(), territory, month))
    else:
        conn.execute("""INSERT OR REPLACE INTO ou_itinerary_months
                        (territory, month, status, read_at, rep, distributor, sfa_url, message, tried_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (territory, month, status, _now(), rep, distributor, sfa_url, message, _now()))
    if status == "ok" and (rep or distributor):
        conn.execute("UPDATE ou_territories SET rep=COALESCE(NULLIF(?, ''), rep), "
                     "distributor=COALESCE(NULLIF(?, ''), distributor) WHERE territory=?", (rep, distributor, territory))
    conn.commit()
    conn.close()


def get_month(territory, month):
    conn = db.get_connection()
    m = conn.execute("SELECT status, read_at, rep, distributor, sfa_url, message FROM ou_itinerary_months "
                     "WHERE territory=? AND month=?", (territory, month)).fetchone()
    days = conn.execute("SELECT day, badge, route FROM ou_itinerary_days WHERE territory=? AND substr(day, 1, 7)=? "
                        "ORDER BY day", (territory, month)).fetchall()
    conn.close()
    info = dict(zip(["status", "read_at", "rep", "distributor", "sfa_url", "message"], m)) if m else None
    return {"territory": territory, "month": month, "info": info,
            "days": [{"day": d, "badge": b, "route": r, "state": _day_state(b, r)} for d, b, r in days]}


def _day_state(badge, route):
    if (badge or "").upper().startswith("H"):
        return "holiday"
    if not route or route.strip() in ("-", "–", "—"):
        return "no_route"
    return "route"


def day_for(territory, day):
    """{state, badge, route, message, read_at} for one territory on one day."""
    mk = day[:7]
    conn = db.get_connection()
    m = conn.execute("SELECT status, read_at, message FROM ou_itinerary_months WHERE territory=? AND month=?",
                     (territory, mk)).fetchone()
    d = conn.execute("SELECT badge, route FROM ou_itinerary_days WHERE territory=? AND day=?", (territory, day)).fetchone()
    conn.close()
    if not m:
        return {"state": "not_read", "badge": "", "route": "", "message": "Not read from SFA yet.", "read_at": ""}
    status, read_at, msg = m
    if status == "missing":
        return {"state": "missing", "badge": "", "route": "", "read_at": read_at,
                "message": msg or f"SFA has no {MONTHS[int(mk[5:]) - 1].title()} itinerary for this territory yet."}
    if status == "failed":
        return {"state": "failed", "badge": "", "route": "", "message": msg, "read_at": read_at}
    if not d:
        return {"state": "no_route", "badge": "", "route": "", "message": "This day isn't in the SFA calendar.",
                "read_at": read_at}
    return {"state": _day_state(*d), "badge": d[0], "route": d[1] if _day_state(*d) == "route" else "",
            "message": msg if msg.startswith("Last read failed") else "", "read_at": read_at}


def board(day):
    """Today's Routes board for one day: every working territory with its route state."""
    out = []
    for t in list_territories(enabled_only=True):
        out.append({**t, **day_for(t["territory"], day), "day": day})
    return out


def month_status_map(territories, months):
    conn = db.get_connection()
    rows = conn.execute("SELECT territory, month, status FROM ou_itinerary_months").fetchall()
    conn.close()
    got = {(t, m): s for t, m, s in rows}
    return {t: {m: got.get((t, m), "") for m in months} for t in territories}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def start_run(kind, trigger, target=""):
    conn = db.get_connection()
    cur = conn.execute("INSERT INTO ou_runs (kind, started_at, trigger, target_day) VALUES (?, ?, ?, ?)",
                       (kind, _now(), trigger, target))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def finish_run(rid, status, message="", log=""):
    conn = db.get_connection()
    conn.execute("UPDATE ou_runs SET finished_at=?, status=?, message=?, log=? WHERE id=?",
                 (_now(), status, message, log, rid))
    conn.commit()
    conn.close()


def list_runs(limit=15):
    conn = db.get_connection()
    rows = conn.execute("SELECT id, kind, started_at, finished_at, trigger, target_day, status, message FROM ou_runs "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(zip(["id", "kind", "started_at", "finished_at", "trigger", "target_day", "status", "message"], r))
            for r in rows]


def get_run_log(rid):
    conn = db.get_connection()
    r = conn.execute("SELECT log FROM ou_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    return r[0] if r else ""


def mark_interrupted():
    conn = db.get_connection()
    conn.execute("UPDATE ou_runs SET status='interrupted', finished_at=?, message='Chimera was closed during this run.' "
                 "WHERE status='running'", (_now(),))
    conn.commit()
    conn.close()


def parse_month_value(text):
    """'NOVEMBER' / 'November' / '11' -> 11 (or None)."""
    t = str(text or "").strip().upper()
    if t.isdigit() and 1 <= int(t) <= 12:
        return int(t)
    for i, m in enumerate(MONTHS):
        if t.startswith(m[:3]):
            return i + 1
    return None


def iso(d):
    return d.isoformat() if isinstance(d, (date, datetime)) else str(d)


def dumps(o):
    return json.dumps(o)
