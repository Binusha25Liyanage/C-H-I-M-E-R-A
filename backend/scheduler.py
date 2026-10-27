"""
scheduler.py
Runs the SFA automation at the configured times.

Two triggers, one rule-set:
  * In-app: a background thread checks every 30 s while Chimera is open.
  * Windows Task Scheduler (optional): one task per run time launches
    Chimera headless ("run_scheduled.py", or "Chimera.exe --scheduled"),
    so runs happen even when the window is closed.

Both go through due_slot(), so Pause, Skip today and the weekday choice
are always respected, and a slot that already ran (by either trigger) is
never run twice. The run lock in automation_store stops two downloads at
once.
"""

import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

import automation_store as AS

GRACE_MINUTES = 45          # a slot still counts as due this long after its time (PC asleep, busy, ...)
TASK_FOLDER = "Chimera"
TASK_PREFIX = "SFA Reports "
_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def due_slot(now=None, settings=None):
    """Returns the 'HH:MM' slot that should run now, or None (with the reason)."""
    now = now or datetime.now()
    s = settings or AS.get_automation_settings()
    if s["paused"] == "1":
        return None, "Automation is paused."
    today = now.date().isoformat()
    if s["skip_date"] == today:
        return None, "Skipping today."
    if s["weekdays"][now.weekday()] != "1":
        return None, "Not a run day."
    due = None
    for t in sorted(s["run_times_list"]):
        h, m = (int(x) for x in t.split(":"))
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if slot_dt <= now < slot_dt + timedelta(minutes=GRACE_MINUTES) and not AS.slot_already_ran(today, t):
            due = t
    return (due, "Due.") if due else (None, "Nothing due.")


def next_run(now=None, settings=None):
    """Next scheduled datetime (ignoring already-ran checks), or None if paused / no times."""
    now = now or datetime.now()
    s = settings or AS.get_automation_settings()
    if s["paused"] == "1" or not s["run_times_list"] or "1" not in s["weekdays"]:
        return None
    for add in range(0, 8):
        d = now.date() + timedelta(days=add)
        if s["weekdays"][d.weekday()] != "1" or s["skip_date"] == d.isoformat():
            continue
        for t in sorted(s["run_times_list"]):
            h, m = (int(x) for x in t.split(":"))
            dt = datetime(d.year, d.month, d.day, h, m)
            if dt > now:
                return dt
    return None


def run_due(trigger="scheduled"):
    """Run the automation if a slot is due. Used by the in-app thread and by Task Scheduler."""
    slot, reason = due_slot()
    if not slot:
        return {"ran": False, "reason": reason}
    import sfa_automation
    result = sfa_automation.run_automation(report_date=date.today().isoformat(), trigger=trigger, slot=slot)
    return {"ran": True, "slot": slot, "result": result}


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
            try:
                import sfa_automation
                if sfa_automation.LIVE.snapshot().get("active"):
                    continue
                run_due("scheduled")
            except Exception:
                pass  # never let the scheduler thread die; the run itself records its errors


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
        if name.startswith(f"\\{TASK_FOLDER}\\{TASK_PREFIX}"):
            names.append(name)
    return names


def sync_windows_tasks(settings=None):
    """
    Makes Windows Task Scheduler match the settings: one task per run time on
    the chosen weekdays, all disabled while paused, none if the option is off.
    Returns {"ok", "messages": [...]}.
    """
    s = settings or AS.get_automation_settings()
    if os.name != "nt":
        return {"ok": False, "messages": ["Windows Task Scheduler is only available on Windows."]}
    msgs, ok_all = [], True
    want = {}
    if s["use_task_scheduler"] == "1":
        days = ",".join(d for d, on in zip(_DAYS, s["weekdays"]) if on == "1") or "MON"
        for t in s["run_times_list"]:
            want[f"\\{TASK_FOLDER}\\{TASK_PREFIX}{t.replace(':', '')}"] = (t, days)
    for name in existing_tasks():
        if name not in want:
            ok, out = _schtasks(["/Delete", "/TN", name, "/F"])
            msgs.append(f"Removed {name}" if ok else f"Couldn't remove {name}: {out}")
            ok_all &= ok
    cmd = scheduled_command()
    for name, (t, days) in want.items():
        ok, out = _schtasks(["/Create", "/F", "/TN", name, "/SC", "WEEKLY", "/D", days, "/ST", t, "/TR", cmd])
        msgs.append(f"Scheduled {t} ({days})" if ok else f"Couldn't schedule {t}: {out}")
        ok_all &= ok
        if ok:
            ok2, out2 = _schtasks(["/Change", "/TN", name, "/DISABLE" if s["paused"] == "1" else "/ENABLE"])
            ok_all &= ok2
            if not ok2:
                msgs.append(f"Couldn't {'disable' if s['paused'] == '1' else 'enable'} {t}: {out2}")
    if not want and not msgs:
        msgs.append("Windows Task Scheduler is not used.")
    return {"ok": ok_all, "messages": msgs}
