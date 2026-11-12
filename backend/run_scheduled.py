"""
run_scheduled.py
Launched by Windows Task Scheduler at each run time. Runs every scheduled
job that is really due (respects Pause, Skip today, holidays, each job's
days, and never repeats a slot the app already ran).

Manual use:  py backend\\run_scheduled.py          (only runs jobs that are due)
             py backend\\run_scheduled.py --now    (run immediately, ignores the schedule)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import sfa_store  # noqa: E402
import report_pipeline  # noqa: E402
import automation_store  # noqa: E402
import schedule_store  # noqa: E402
import scheduler  # noqa: E402


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    db.init_db()
    sfa_store.init_schema()
    report_pipeline.init_schema()
    automation_store.init_schema()
    schedule_store.init_schema()
    if "--now" in argv:
        import sfa_automation
        r = sfa_automation.run_automation(trigger="cli")
        print(f"{r['status'].upper()}: {r.get('message', '')}")
        return 0 if r.get("ok") else 1
    try:                                   # WhatsApp reminders due now (e.g. 15 minutes before a report)
        import reminders
        reminders.init_schema()
        rr = reminders.run_due()
        if rr.get("sent") or rr.get("skipped") or rr.get("missed"):
            print(f"Reminders: {rr.get('sent', 0)} sent, {rr.get('skipped', 0) + rr.get('missed', 0)} skipped.")
    except Exception as e:
        print(f"Reminders: {e}")
    r = scheduler.run_due("scheduled")
    if not r["ran"]:
        print(f"Nothing to do: {r['reason']}")
        return 0
    code = 0
    for x in r["results"]:
        res = x["result"]
        print(f"{x['job']} at {x['slot']}: {res.get('status', '').upper()} — {res.get('message', '')}")
        code |= 0 if res.get("ok") else 1
    return code


if __name__ == "__main__":
    sys.exit(main())
