"""
reminders.py
WhatsApp reminder messages, e.g. before each Daily Sales Report:

  "❗ Please sync your bills for the *12.15 PM Sales Report Live Update* as
   soon as possible to keep the report accurate and up to date."

A reminder has its own text, its own chats (they can be groups that never
get reports), and its own times:

  * linked to a scheduled report job — sent N minutes (default 15) before
    each of that job's times; change the job's times and the reminders
    follow. {report_time} is the time of that report ("12.15 PM");
  * plus any extra times you add, sent at that time ({report_time} = the
    job's next time after it, if the reminder is linked to a job).

A reminder is only sent when its report run is really going to happen: not
while automation is paused or today is skipped, not when the job is off,
switched off by its type, or doesn't run that day (weekday / holiday). Those
are recorded as skipped. Each send time goes once a day (a record is kept),
and while Chimera was off a reminder still goes late — but never after its
report time has passed.

Placeholders: {report_time} (12.15 PM), {date} (today), {day} (Thursday).
WhatsApp's own *bold* and _italic_ work as usual.
"""

import json
import threading
from datetime import date, datetime, timedelta

import automation_store as AS
import db
import schedule_store as SS

DEFAULT_TEXT = ("❗ Please sync your bills for the *{report_time} Sales Report Live Update* as soon as possible to keep "
                "the report accurate and up to date.")
LATE_MINUTES = 30            # a reminder with no report time still goes up to this late
_send_lock = threading.Lock()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_schema():
    conn = db.get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wa_reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            text        TEXT NOT NULL,
            job_id      INTEGER,                    -- scheduled report job it goes before (NULL = own times only)
            offset_min  INTEGER NOT NULL DEFAULT 15,
            extra_times TEXT NOT NULL DEFAULT '',   -- 'HH:MM,HH:MM'
            chats       TEXT NOT NULL DEFAULT '[]',
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL
        )""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wa_reminders)")}
    if "active_from" not in cols:     # times before a reminder was saved / switched on don't count as missed
        conn.execute("ALTER TABLE wa_reminders ADD COLUMN active_from TEXT NOT NULL DEFAULT ''")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wa_reminder_sends (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            reminder_id INTEGER NOT NULL,
            day         TEXT NOT NULL,
            send_at     TEXT NOT NULL,              -- 'HH:MM' it was planned for
            report_time TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL,              -- sent | partial | failed | skipped
            chats       TEXT NOT NULL DEFAULT '[]', -- [{chat, ok, message}]
            text        TEXT NOT NULL DEFAULT '',
            message     TEXT NOT NULL DEFAULT '',
            at          TEXT NOT NULL,
            UNIQUE(reminder_id, day, send_at)
        )""")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------
_COLS = ["id", "name", "text", "job_id", "offset_min", "extra_times", "chats", "enabled", "created_at", "active_from"]


def _row(r):
    x = dict(zip(_COLS, r))
    x["chats"] = json.loads(x["chats"] or "[]")
    x["enabled"] = bool(x["enabled"])
    x["extra_list"] = [t for t in (x["extra_times"] or "").split(",") if t]
    return x


def list_reminders():
    conn = db.get_connection()
    rows = [_row(r) for r in conn.execute(f"SELECT {', '.join(_COLS)} FROM wa_reminders ORDER BY id")]
    conn.close()
    jobs = {j["id"]: j for j in SS.list_jobs()}
    for r in rows:
        j = jobs.get(r["job_id"])
        r["job_name"] = j["name"] if j else ""
        r["job_times"] = j["times_list"] if j else []
        r["job_missing"] = bool(r["job_id"]) and not j
    return rows


def get_reminder(rid):
    return next((r for r in list_reminders() if r["id"] == int(rid)), None)


def _clean_times(v):
    out = set()
    for t in (v if isinstance(v, (list, tuple)) else str(v or "").replace(";", ",").split(",")):
        t = str(t).strip()
        if not t:
            continue
        try:
            h, m = t.split(":")
            h, m = int(h), int(m)
            assert 0 <= h < 24 and 0 <= m < 60
        except Exception:
            raise ValueError(f"'{t}' isn't a time (use HH:MM, e.g. 11:55).")
        out.add(f"{h:02d}:{m:02d}")
    return sorted(out)


def save_reminder(data):
    name = str(data.get("name") or "").strip() or "Reminder"
    text = str(data.get("text") or "").replace("\r\n", "\n").strip()
    if not text:
        return False, "Write the message.", None
    if len(text) > 2000:
        return False, "The message is too long (2,000 characters at most).", None
    job_id = data.get("job_id")
    job_id = int(job_id) if job_id not in (None, "", 0, "0") else None
    if job_id and not SS.get_job(job_id):
        return False, "That scheduled job no longer exists.", None
    try:
        offset = int(data.get("offset_min", 15))
    except (TypeError, ValueError):
        return False, "Minutes before must be a number.", None
    if not 0 <= offset <= 240:
        return False, "Minutes before must be between 0 and 240.", None
    try:
        extra = _clean_times(data.get("extra_times", ""))
    except ValueError as e:
        return False, str(e), None
    if not job_id and not extra:
        return False, "Pick the reports it goes before, or add at least one time.", None
    chats = [str(c).strip() for c in (data.get("chats") or []) if str(c).strip()]
    chats = list(dict.fromkeys(chats))
    if not chats:
        return False, "Pick at least one chat or group.", None
    vals = (name, text, job_id, offset, ",".join(extra), json.dumps(chats), 1 if data.get("enabled", True) else 0)
    conn = db.get_connection()
    if data.get("id"):
        n = conn.execute("UPDATE wa_reminders SET name=?, text=?, job_id=?, offset_min=?, extra_times=?, chats=?, "
                         "enabled=?, active_from=? WHERE id=?", vals + (_now(), int(data["id"]))).rowcount
        rid = int(data["id"])
        if not n:
            conn.close()
            return False, "That reminder no longer exists.", None
    else:
        rid = conn.execute("INSERT INTO wa_reminders (name, text, job_id, offset_min, extra_times, chats, enabled, "
                           "created_at, active_from) VALUES (?,?,?,?,?,?,?,?,?)", vals + (_now(), _now())).lastrowid
    conn.commit()
    conn.close()
    return True, "Reminder saved.", rid


def set_enabled(rid, enabled):
    conn = db.get_connection()
    if enabled:
        conn.execute("UPDATE wa_reminders SET enabled=1, active_from=? WHERE id=? AND enabled=0", (_now(), int(rid)))
    else:
        conn.execute("UPDATE wa_reminders SET enabled=0 WHERE id=?", (int(rid),))
    conn.commit()
    conn.close()


def delete_reminder(rid):
    conn = db.get_connection()
    conn.execute("DELETE FROM wa_reminders WHERE id=?", (int(rid),))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------
def fmt_time(hhmm):
    """'14:15' -> '2.15 PM' (as the reps' messages write it)."""
    if not hhmm:
        return ""
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h % 12) or 12}.{m:02d} {'AM' if h < 12 else 'PM'}"


def render(text, report_time, when=None):
    when = when or datetime.now()
    return (text.replace("{report_time}", fmt_time(report_time) or when.strftime("%H:%M"))
            .replace("{date}", when.date().isoformat()).replace("{day}", when.strftime("%A")))


# ---------------------------------------------------------------------------
# The day's plan
# ---------------------------------------------------------------------------
def _block(d, settings):
    if settings.get("paused") == "1":
        return "automation is paused"
    if settings.get("skip_date") == d.isoformat():
        return "today is skipped"
    return None


def _job_reason(job, d, settings, hol):
    if job is None:
        return "its scheduled job was deleted"
    if not job["enabled"]:
        return f"'{job['name']}' is switched off"
    if not SS.type_on(job, settings):
        return f"{SS.JOB_TYPES.get(job['job_type'], {}).get('label', 'that kind of job')} is switched off in Automations"
    if not SS.runs_on(job, d, hol):
        hn = SS.holiday_name(d) if d.isoformat() in hol else None
        return f"holiday ({hn})" if hn else f"'{job['name']}' doesn't run on {d:%A}s"
    return None


def plan_day(d=None, settings=None, reminders=None):
    """Every reminder send for date d: {reminder_id, name, slot 'HH:MM', at (datetime), report_time, chats,
    skip (reason or None), done (status recorded for it, or None)}."""
    d = d or date.today()
    s = settings or AS.get_automation_settings()
    hol = SS.holiday_set()
    block = _block(d, s)
    done = {(r[0], r[1]): r[2] for r in _q("SELECT reminder_id, send_at, status FROM wa_reminder_sends WHERE day=?",
                                           (d.isoformat(),))}
    out = []
    for r in (reminders if reminders is not None else list_reminders()):
        if not r["enabled"]:
            continue
        job = SS.get_job(r["job_id"]) if r["job_id"] else None
        jreason = _job_reason(job, d, s, hol) if r["job_id"] else None
        times = []                                         # (send 'HH:MM', report 'HH:MM')
        if job:
            for t in job["times_list"]:
                h, m = (int(x) for x in t.split(":"))
                at = datetime(d.year, d.month, d.day, h, m) - timedelta(minutes=r["offset_min"])
                if at.date() == d:
                    times.append((at.strftime("%H:%M"), t))
        for t in r["extra_list"]:
            nxt = [x for x in (job["times_list"] if job else []) if x >= t]
            times.append((t, nxt[0] if nxt else ""))
        for slot, rep in sorted(set(times)):
            h, m = (int(x) for x in slot.split(":"))
            reason = block or jreason
            out.append({"reminder_id": r["id"], "name": r["name"], "slot": slot, "report_time": rep,
                        "active_from": r.get("active_from") or "",
                        "at": datetime(d.year, d.month, d.day, h, m), "chats": r["chats"], "text": r["text"],
                        "skip": reason, "done": done.get((r["id"], slot))})
    out.sort(key=lambda x: (x["at"], x["reminder_id"]))
    return out


def _q(sql, args=()):
    conn = db.get_connection()
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:
        return []
    finally:
        conn.close()


def _deadline(item):
    if item["report_time"]:
        h, m = (int(x) for x in item["report_time"].split(":"))
        rep = item["at"].replace(hour=h, minute=m)
        if rep > item["at"]:
            return rep
    return item["at"] + timedelta(minutes=LATE_MINUTES)


def due(now=None, settings=None):
    """(to_send, to_skip, missed) right now."""
    now = now or datetime.now()
    send, skip, missed = [], [], []
    for it in plan_day(now.date(), settings):
        if it["done"] or it["at"] > now:
            continue
        if it["active_from"] and it["at"] < datetime.fromisoformat(it["active_from"]).replace(second=0):
            continue                     # the reminder didn't exist (or was off / different) at that time
        if it["skip"]:
            skip.append(it)
        elif now < _deadline(it):
            send.append(it)
        else:
            missed.append(it)
    return send, skip, missed


def _record(it, status, chats, text, message):
    conn = db.get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO wa_reminder_sends (reminder_id, day, send_at, report_time, status, chats, "
                     "text, message, at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (it["reminder_id"], it["at"].date().isoformat(), it["slot"], it["report_time"], status,
                      json.dumps(chats), text, message, _now()))
        conn.commit()
    finally:
        conn.close()


def recent_sends(limit=40, day=None):
    rows = _q("SELECT s.id, s.reminder_id, r.name, s.day, s.send_at, s.report_time, s.status, s.chats, s.text, "
              "s.message, s.at FROM wa_reminder_sends s LEFT JOIN wa_reminders r ON r.id=s.reminder_id "
              + ("WHERE s.day=? " if day else "") + "ORDER BY s.id DESC LIMIT ?", ((day, limit) if day else (limit,)))
    return [{"id": a, "reminder_id": b, "name": c or "(deleted reminder)", "day": d, "send_at": e, "report_time": f,
             "status": g, "chats": json.loads(h or "[]"), "text": i, "message": j, "at": k}
            for a, b, c, d, e, f, g, h, i, j, k in rows]


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def _notify(kind, title, body):
    try:
        import desktop_notify
        desktop_notify.event(kind, "whatsapp", title, body, action="whatsapp")
    except Exception:
        pass


def run_due(now=None, wait=True):
    """Sends what's due (one WhatsApp session for all of it), records skipped and missed ones.
    Returns {sent, skipped, missed, busy}."""
    if not _send_lock.acquire(blocking=False):
        return {"busy": True, "sent": 0, "skipped": 0, "missed": 0}
    try:
        now = now or datetime.now()
        send, skip, missed = due(now)
        for it in skip:
            _record(it, "skipped", [], "", f"Not sent — {it['skip']}.")
        for it in missed:
            _record(it, "skipped", [], "", "Not sent — Chimera wasn't running until after the report time.")
        if not send:
            return {"busy": False, "sent": 0, "skipped": len(skip), "missed": len(missed)}
        res = send_items(send, wait=wait)
        return {"busy": res.get("busy", False), "sent": res.get("sent", 0), "skipped": len(skip), "missed": len(missed)}
    finally:
        _send_lock.release()


def send_items(items, wait=True, test=False):
    """Sends each item's text to each of its chats in one WhatsApp session and records the results."""
    import whatsapp_sender as W
    s = AS.get_automation_settings()
    state = {"sent": 0, "busy": False}

    async def fn(page):
        total_fail = 0
        for it in items:
            text = render(it["text"], it["report_time"], it["at"] if not test else datetime.now())
            results = []
            for chat in it["chats"]:
                try:
                    await W._open_chat(page, chat)
                    await W._send_text(page, text, "The reminder")
                    results.append({"chat": chat, "ok": True, "message": ""})
                    W._log_delivery(None, chat, 0, "sent", f"Reminder: {it['name']}" + (" (test)" if test else ""))
                    W.WA.log(f"{chat}: reminder '{it['name']}' sent.")
                except Exception as e:
                    msg = str(e).splitlines()[0][:200]
                    results.append({"chat": chat, "ok": False, "message": msg})
                    W._log_delivery(None, chat, 0, "failed", f"Reminder: {it['name']} — {msg}")
                    W.WA.log(f"{chat}: reminder '{it['name']}' failed — {msg}", "error")
            ok = sum(1 for x in results if x["ok"])
            status = "sent" if ok == len(results) else "partial" if ok else "failed"
            total_fail += len(results) - ok
            if not test:
                _record(it, status, results, text, f"Sent to {ok} of {len(results)} chat(s).")
            state["sent"] += 1 if ok else 0
            who = ", ".join(x["chat"] for x in results if x["ok"])
            if status == "sent":
                _notify("finished", f"Reminder sent — {it['name']}" + (f" ({fmt_time(it['report_time'])} report)"
                                                                        if it["report_time"] else ""), f"To {who}.")
            else:
                _notify("failed", f"Reminder {'partly ' if ok else ''}not sent — {it['name']}",
                        "; ".join(f"{x['chat']}: {x['message']}" for x in results if not x["ok"]))
        return {"ok": total_fail == 0, "message": f"{state['sent']} reminder(s) sent"
                                                  + (f", {total_fail} chat(s) failed" if total_fail else "") + "."}

    if not W.WA.start("reminder"):
        state["busy"] = True          # tried again on the next check, until the report time
        return state
    if wait:
        W._run("reminder", s.get("wa_show_browser") != "1", fn)
    else:
        threading.Thread(target=lambda: W._run("reminder", s.get("wa_show_browser") != "1", fn), daemon=True,
                         name="wa-reminder").start()
    return state


def send_test(rid):
    """Sends a reminder now to its chats (not recorded as a scheduled send)."""
    r = get_reminder(rid)
    if not r:
        return {"ok": False, "message": "That reminder no longer exists."}
    nxt = [x for x in (r["job_times"] or []) if x >= datetime.now().strftime("%H:%M")]
    item = {"reminder_id": r["id"], "name": r["name"], "slot": datetime.now().strftime("%H:%M"),
            "report_time": nxt[0] if nxt else (r["job_times"][0] if r["job_times"] else ""), "at": datetime.now(),
            "chats": r["chats"], "text": r["text"]}
    res = send_items([item], wait=False, test=True)
    if res["busy"]:
        return {"ok": False, "message": "WhatsApp is busy with another task — try again in a moment."}
    return {"ok": True, "message": "Sending the reminder now…"}


def task_times():
    """Times Windows must wake Chimera for reminders (today's plan for every enabled reminder, any day)."""
    out = set()
    for r in list_reminders():
        if not r["enabled"]:
            continue
        job = SS.get_job(r["job_id"]) if r["job_id"] else None
        for t in (job["times_list"] if job else []):
            h, m = (int(x) for x in t.split(":"))
            at = datetime(2000, 1, 2, h, m) - timedelta(minutes=r["offset_min"])
            if at.day == 2:
                out.add(at.strftime("%H:%M"))
        out.update(r["extra_list"])
    return sorted(out)
