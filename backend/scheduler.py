"""
scheduler.py
Runs scheduled jobs (schedule_store) at their times.

Two triggers, one rule-set:
  * In-app: a background thread checks the clock every 30 s while Chimera
    is open.
  * Windows Task Scheduler (optional): one daily task per distinct run time
    launches Chimera headless ("run_scheduled.py", or "Chimera.exe
    --scheduled") so jobs run even when the window is closed. The task only
    wakes Chimera up; the same rules below decide whether anything is due,
    which is why one daily task per time also covers weekly, monthly and
    one-time jobs.

Both go through due_jobs(), so Pause, Skip today, holidays and each job's
own days are always respected, and a slot that already fired (by either
trigger) never fires again.

Clock: everything uses the PC's clock. Chimera compares it with the SFA
server's clock on every SFA login (and on demand) and warns if they differ.
"""

import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import automation_store as AS
import schedule_store as SS

GRACE_MINUTES = 45          # a slot still counts as due this long after its time (PC asleep, busy, ...)
TASK_FOLDER = "Chimera"
TASK_PREFIX = "Chimera run "
_OLD_PREFIXES = ("SFA Reports ",)   # task names used by earlier versions, cleaned up on sync
CLOCK_WARN_SECONDS = 60
CLOCK_BAD_SECONDS = 300


def _global_block(d, settings):
    if settings["paused"] == "1":
        return "Automation is paused."
    if settings["skip_date"] == d.isoformat():
        return "Skipping today."
    return None


def due_jobs(now=None, settings=None, jobs=None):
    """[(job, 'HH:MM')] that should fire now, plus a reason when nothing is due."""
    now = now or datetime.now()
    s = settings or AS.get_automation_settings()
    block = _global_block(now.date(), s)
    if block:
        return [], block
    today = now.date().isoformat()
    hol = SS.holiday_set()
    due = []
    for j in (jobs if jobs is not None else SS.list_jobs()):
        if not SS.type_on(j, s) or not SS.runs_on(j, now.date(), hol):
            continue
        for t in j["times_list"]:
            h, m = (int(x) for x in t.split(":"))
            slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot_dt <= now < slot_dt + timedelta(minutes=GRACE_MINUTES) and not SS.already_fired(j["id"], today, t):
                due.append((j, t))
    if not due:
        name = SS.holiday_name(now.date()) if now.date().isoformat() in hol else None
        return [], (f"Holiday: {name}." if name else "Nothing due.")
    return due, "Due."


def upcoming(now=None, days=7, settings=None, limit=None, include_skipped=False):
    """Scheduled occurrences from now on, with Pause / Skip today applied."""
    now = now or datetime.now()
    s = settings or AS.get_automation_settings()
    if s["paused"] == "1":
        return []
    occ = SS.occurrences(now, now + timedelta(days=days), include_skipped=include_skipped)
    occ = [o for o in occ if o["at"] > now and s["skip_date"] != o["at"].date().isoformat() and SS.type_on(o["job"], s)]
    return occ[:limit] if limit else occ


def next_run(now=None, settings=None):
    """Next scheduled datetime across all jobs, or None (paused / nothing scheduled within a year)."""
    nxt = next_occurrence(now, settings)
    return nxt["at"] if nxt else None


def next_occurrence(now=None, settings=None):
    now = now or datetime.now()
    for span in (8, 40, 370):           # cheap first: most jobs run within a week
        occ = upcoming(now, span, settings, limit=1)
        if occ:
            return occ[0]
    return None


def next_run_per_job(now=None, settings=None):
    now = now or datetime.now()
    s = settings or AS.get_automation_settings()
    out = {}
    if s["paused"] == "1":
        return out
    for o in SS.occurrences(now, now + timedelta(days=370)):
        if o["at"] > now and s["skip_date"] != o["at"].date().isoformat() and SS.type_on(o["job"], s):
            out.setdefault(o["job"]["id"], o["at"])
    return out


def _run_job(job, slot, trigger):
    if job["job_type"] == "sfa_downloads":
        import sfa_automation
        day = date.today() - timedelta(days=1 if job["options"].get("report_day") == "yesterday" else 0)
        opts = job["options"]
        # Approval is set per time on the job ("Ask me first"); jobs saved before that use Daily Reports' setting.
        review = (slot in opts["review_times"]) if "review_times" in opts else None
        r = sfa_automation.run_automation(report_date=day.isoformat(), trigger=trigger, slot=slot,
                                          profile_ids=opts.get("profile_ids") or None, review=review,
                                          report_kinds=opts.get("report_kinds") or None)
        return r
    if SS.JOB_TYPES.get(job["job_type"], {}).get("report"):
        import sfa_automation
        kind, ids, missing = SS.report_job_plan(job)
        if missing or not ids:
            import sfa_store
            return {"ok": False, "status": "failed",
                    "message": "No enabled download for: " + ", ".join(sfa_store.DATASET_LABELS[m] for m in missing)
                               + " — turn it on in Report Downloads."}
        day = date.today() - timedelta(days=1 if job["options"].get("report_day") == "yesterday" else 0)
        opts = job["options"]
        review = (slot in opts["review_times"]) if "review_times" in opts else None
        return sfa_automation.run_automation(report_date=day.isoformat(), trigger=trigger, slot=slot,
                                             profile_ids=ids, review=review, report_kinds=[kind])
    if job["job_type"] == "rep_attendance":
        import rep_attendance_automation
        review = slot in (job["options"].get("review_times") or [])
        return rep_attendance_automation.run(trigger=trigger, slot=slot, review=review, send=True)
    if job["job_type"] == "distributors":
        import distributor_sync
        return distributor_sync.run_sync(trigger=trigger)
    if job["job_type"] == "loadings":
        import loading_automation
        return loading_automation.run_prepare(trigger=trigger)
    return {"ok": False, "status": "skipped", "message": f"{job['type_label']} isn't available yet."}


# ---------------------------------------------------------------------------
# Lanes: different kinds of job run side by side; the same kind queues instead of being missed
# ---------------------------------------------------------------------------
QUEUE_MAX_WAIT_S = 4 * 60 * 60        # a queued job that still can't start after this is given up (Missed)
_QUEUES = {}                           # worker lane -> {"q": deque, "thread": Thread|None}
_QLOCK = threading.Lock()


def _worker_lane(job):
    import lanes
    return lanes.job_lane(job["job_type"]) if AS.side_by_side() else "sfa"


def _notify_started(job, slot, today):
    try:
        import desktop_notify
        desktop_notify.event("started", "schedule", f"{job['name']} — {slot}",
                             f"Scheduled time reached — {job.get('type_label') or 'job'} is starting now.",
                             action={"sfa_downloads": "automation", "loadings": "loadings", "distributors": "loadings",
                                     "rep_attendance": "rep-attendance"}
                             .get(job["job_type"], "automation" if job["job_type"].startswith("report_") else "schedules"),
                             tag=f"sched-{job['id']}-{today}-{slot}")
    except Exception:
        pass


def _notify_missed(job, slot, today, why):
    try:
        import desktop_notify
        desktop_notify.event("failed", "schedule", f"Missed: {job['name']} — {slot}", why + " Use Run now in Schedules.",
                             action="schedules", tag=f"missed-{job['id']}-{today}-{slot}")
    except Exception:
        pass


def _run_queued(job, slot, trigger, today):
    """Wait until this kind of job may start (a manual run of the same kind, or — side by side off — any SFA job),
    then run it. Never dropped because something else was busy; given up only after QUEUE_MAX_WAIT_S."""
    import lanes
    lane = lanes.job_lane(job["job_type"])
    t0, told = time.time(), False
    for attempt in (1, 2):
        while True:
            busy = lanes.busy_message(lane)
            if not busy:
                break
            if not told:
                SS.set_fire_outcome(job["id"], today, slot, f"queued: waiting — {busy} [pid {os.getpid()}]")
                told = True
            if time.time() - t0 > QUEUE_MAX_WAIT_S:
                why = f"Waited {QUEUE_MAX_WAIT_S // 3600} hours for the other job to finish."
                SS.set_fire_outcome(job["id"], today, slot, f"missed: {why}")
                _notify_missed(job, slot, today, why)
                return {"ok": False, "status": "missed", "message": why}
            time.sleep(QUEUE_POLL_S)
        _notify_started(job, slot, today)
        try:
            r = _run_job(job, slot, trigger)
        except Exception as e:          # recorded, and the next job still runs
            r = {"ok": False, "status": "failed", "message": str(e)}
        if r.get("status") != "busy" or attempt == 2:
            break                       # (busy = something grabbed SFA between our check and the start: wait again)
    SS.set_fire_outcome(job["id"], today, slot, f"{r.get('status', '')}: {r.get('message', '')}")
    return r


QUEUE_POLL_S = 5


def _drain(lane, results):
    while True:
        with _QLOCK:
            e = _QUEUES[lane]
            if not e["q"]:
                e["thread"] = None
                return
            job, slot, trigger, today = e["q"].popleft()
        r = _run_queued(job, slot, trigger, today)
        results.append({"job": job["name"], "slot": slot, "result": r})


def _enqueue(job, slot, trigger, today, results):
    from collections import deque
    lane = _worker_lane(job)
    with _QLOCK:
        e = _QUEUES.setdefault(lane, {"q": deque(), "thread": None})
        e["q"].append((job, slot, trigger, today))
        if e["thread"] is None or not e["thread"].is_alive():
            e["thread"] = threading.Thread(target=_drain, args=(lane, results), daemon=True, name=f"chimera-lane-{lane}")
            e["thread"].start()
        return e["thread"]


def queue_snapshot():
    """What's waiting, per lane (for the screen)."""
    with _QLOCK:
        return {lane: [{"job": j["name"], "slot": s} for j, s, _t, _d in e["q"]] for lane, e in _QUEUES.items() if e["q"]}


def run_due(trigger="scheduled", now=None, wait=True):
    """Fire every job that is due: each kind of job in its own lane (side by side), the same kind one after the
    other. wait=True (Task Scheduler, tests): return when they've all finished. The in-app thread doesn't wait."""
    due, reason = due_jobs(now)
    if not due:
        return {"ran": False, "reason": reason, "results": []}
    results, threads = [], []
    today = (now or datetime.now()).date().isoformat()
    for job, slot in due:
        if not SS.record_fire(job["id"], today, slot, trigger):
            continue        # the other trigger got there first
        SS.set_fire_outcome(job["id"], today, slot, f"queued: about to start [pid {os.getpid()}]")
        threads.append(_enqueue(job, slot, trigger, today, results))
    if wait:
        for t in {id(t): t for t in threads}.values():
            t.join()
    return {"ran": bool(threads), "reason": reason, "results": results}


# ---------------------------------------------------------------------------
# Missed runs: shown in Schedules (with Run now) and as a notification
# ---------------------------------------------------------------------------
def mark_missed(now=None):
    """Slots whose time + grace has passed today without running (PC off, Chimera closed and no Task Scheduler,
    or Chimera closed while the job was waiting in its queue)."""
    now = now or datetime.now()
    today = now.date().isoformat()
    marked = []
    # waiting in a queue of a Chimera that's gone
    for f in SS.queued_fires():
        pid = 0
        try:
            pid = int(f["outcome"].rsplit("[pid ", 1)[1].rstrip("]"))
        except (IndexError, ValueError):
            pass
        if pid != os.getpid() and not AS.pid_alive(pid):
            SS.set_fire_outcome(f["job_id"], f["day"], f["slot"], "missed: Chimera was closed before it could run.")
            marked.append((f["job_id"], f["slot"]))
    s = AS.get_automation_settings()
    if _global_block(now.date(), s):
        return marked
    hol = SS.holiday_set()
    for j in SS.list_jobs():
        if not SS.type_on(j, s) or not SS.runs_on(j, now.date(), hol):
            continue
        since = max(j.get("created_at") or "", j.get("updated_at") or "")
        for t in j["times_list"]:
            h, m = (int(x) for x in t.split(":"))
            slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot_dt + timedelta(minutes=GRACE_MINUTES) > now or (since and slot_dt.isoformat() < since):
                continue
            if SS.already_fired(j["id"], today, t):
                continue
            if SS.record_fire(j["id"], today, t, "missed"):
                why = "Chimera wasn't running at that time (and Task Scheduler didn't start it)."
                SS.set_fire_outcome(j["id"], today, t, f"missed: {why}")
                _notify_missed(j, t, today, why)
                marked.append((j["id"], t))
    return marked


class InAppScheduler:
    def __init__(self, interval=30):
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="chimera-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:                # WhatsApp reminders go whatever else is running (they use WhatsApp, not SFA)
                import reminders
                threading.Thread(target=reminders.run_due, daemon=True, name="chimera-reminders").start()
            except Exception:
                pass
            try:
                run_due("scheduled", wait=False)       # each kind of job in its own lane; same kind queues
                mark_missed()
                # Outlet Updates: read the itinerary for the day being prepared (23:59 onwards = tomorrow) —
                # and if Chimera wasn't running then, now. It has its own lane.
                import lanes
                import itinerary_automation
                if not lanes.busy_message("outlets"):
                    itinerary_automation.ensure_prepared()
            except Exception:
                pass  # never let the scheduler thread die; the run itself records its errors


# ---------------------------------------------------------------------------
# Clock check
# ---------------------------------------------------------------------------
def record_clock(server_date_header, source):
    """Store the PC-vs-server difference from an HTTP 'Date' header. Returns offset seconds or None."""
    if not server_date_header:
        return None
    try:
        server = parsedate_to_datetime(server_date_header)
    except (TypeError, ValueError):
        return None
    local = datetime.now().astimezone()
    offset = round((server - local).total_seconds())
    AS.save_automation_settings({"clock_offset_s": str(offset), "clock_source": source,
                                 "clock_checked_at": datetime.now().isoformat(timespec="seconds")})
    return offset


def check_clock_now(url=None, timeout=10):
    """Ask the SFA server for its time (HEAD request to the login URL)."""
    if not url:
        import sfa_config
        url = sfa_config.load_config().get("login_url")
    if not url:
        return {"ok": False, "message": "Enter the SFA login URL in Settings first."}
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Chimera clock check"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            hdr = r.headers.get("Date")
    except urllib.error.HTTPError as e:      # any HTTP answer still carries the server's time
        hdr = e.headers.get("Date")
    except Exception as e:
        return {"ok": False, "message": f"Couldn't reach SFA to compare clocks: {e}"}
    off = record_clock(hdr, "SFA server")
    if off is None:
        return {"ok": False, "message": "SFA didn't report its time."}
    return {"ok": True, **clock_status()}


def clock_status(settings=None):
    s = settings or AS.get_automation_settings()
    now = datetime.now()
    out = {"now": now.isoformat(timespec="seconds"), "tz": now.astimezone().strftime("UTC%z"),
           "offset_s": None, "checked_at": s.get("clock_checked_at") or None, "source": s.get("clock_source") or None,
           "level": "unknown", "message": "Not checked yet — Chimera compares with SFA's clock on every SFA login."}
    if s.get("clock_offset_s") not in (None, ""):
        off = int(s["clock_offset_s"])
        out["offset_s"] = off
        a = abs(off)
        ahead_behind = "behind" if off > 0 else "ahead of"
        if a <= CLOCK_WARN_SECONDS:
            out["level"], out["message"] = "ok", "PC clock matches SFA."
        elif a <= CLOCK_BAD_SECONDS:
            out["level"] = "warn"
            out["message"] = f"PC clock is {a // 60} min {a % 60} s {ahead_behind} SFA. Sync the Windows clock."
        else:
            out["level"] = "bad"
            out["message"] = (f"PC clock is {a // 60} minutes {ahead_behind} SFA — scheduled runs will start at "
                              f"the wrong time. In Windows: Settings › Time & language › Date & time › Sync now.")
    return out


# ---------------------------------------------------------------------------
# Windows Task Scheduler
# ---------------------------------------------------------------------------
def scheduled_command():
    """What Task Scheduler should launch for a headless scheduled run."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --scheduled'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_scheduled.py")
    return f'"{pyw if os.path.exists(pyw) else exe}" "{runner}"'


def _schtasks(args):
    if os.name != "nt":
        return False, "Windows Task Scheduler is only available on Windows."
    try:
        r = subprocess.run(["schtasks"] + args, capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0, (r.stdout or r.stderr).strip()
    except Exception as e:
        return False, str(e)


def existing_tasks():
    ok, out = _schtasks(["/Query", "/FO", "CSV", "/NH"])
    if not ok:
        return []
    names = []
    for line in out.splitlines():
        name = line.split(",")[0].strip('"')
        if any(name.startswith(f"\\{TASK_FOLDER}\\{p}") for p in (TASK_PREFIX,) + _OLD_PREFIXES):
            names.append(name)
    return names


def wanted_task_times(now=None):
    """Distinct times at which Windows must wake Chimera (enabled jobs that can still run)."""
    now = now or datetime.now()
    times = set()
    for j in SS.list_jobs():
        if not j["enabled"]:
            continue
        if j["frequency"] == "once" and j["once_date"] < now.date().isoformat():
            continue
        times.update(j["times_list"])
    try:                        # WhatsApp reminders (e.g. 15 minutes before each report)
        import reminders
        times.update(reminders.task_times())
    except Exception:
        pass
    return sorted(times)


def sync_windows_tasks(settings=None):
    """
    Makes Windows Task Scheduler match the jobs: one daily task per distinct
    run time, all disabled while paused, none if the option is off.
    Returns {"ok", "messages": [...]}.
    """
    s = settings or AS.get_automation_settings()
    if os.name != "nt":
        return {"ok": False, "messages": ["Windows Task Scheduler is only available on Windows."]}
    msgs, ok_all = [], True
    want = {}
    if s["use_task_scheduler"] == "1":
        for t in wanted_task_times():
            want[f"\\{TASK_FOLDER}\\{TASK_PREFIX}{t.replace(':', '')}"] = t
    for name in existing_tasks():
        if name not in want:
            ok, out = _schtasks(["/Delete", "/TN", name, "/F"])
            msgs.append(f"Removed {name}" if ok else f"Couldn't remove {name}: {out}")
            ok_all &= ok
    cmd = scheduled_command()
    for name, t in want.items():
        ok, out = _schtasks(["/Create", "/F", "/TN", name, "/SC", "DAILY", "/ST", t, "/TR", cmd])
        msgs.append(f"Windows will start Chimera daily at {t}" if ok else f"Couldn't schedule {t}: {out}")
        ok_all &= ok
        if ok:
            ok2, out2 = _schtasks(["/Change", "/TN", name, "/DISABLE" if s["paused"] == "1" else "/ENABLE"])
            ok_all &= ok2
            if not ok2:
                msgs.append(f"Couldn't {'disable' if s['paused'] == '1' else 'enable'} {t}: {out2}")
    if not want and not msgs:
        msgs.append("Windows Task Scheduler is not used.")
    return {"ok": ok_all, "messages": msgs}
