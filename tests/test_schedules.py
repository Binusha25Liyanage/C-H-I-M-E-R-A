"""
Tests for scheduled jobs: frequencies, holidays, Pause / Skip today, the
grace window, never firing a slot twice, the legacy-schedule migration and
the clock check. No browser needed.

Run:  py -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

_TMP_HOME = tempfile.mkdtemp()
os.environ["HOME"] = _TMP_HOME
os.environ["USERPROFILE"] = _TMP_HOME

import db  # noqa: E402
db.APP_DATA_DIR = os.path.join(_TMP_HOME, "Chimera", "data")
db.DB_PATH = os.path.join(db.APP_DATA_DIR, "chimera.db")
import automation_store as AS  # noqa: E402
import schedule_store as SS  # noqa: E402
import scheduler  # noqa: E402


def job(**kw):
    base = {"name": "Test job", "job_type": "sfa_downloads", "frequency": "daily", "times": "10:00",
            "enabled": True, "skip_holidays": True, "options": {"report_day": "today"}}
    base.update(kw)
    ok, msg, j = SS.save_job(base)
    assert ok, msg
    return j


class ScheduleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()

    def setUp(self):
        conn = db.get_connection()
        for t in ("schedule_jobs", "holidays", "schedule_fires"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("DELETE FROM automation_settings WHERE key='schedule_migrated'")
        conn.commit()
        conn.close()
        AS.save_automation_settings({"paused": "0", "skip_date": "", "run_times": "10:00,15:00", "weekdays": "1111100"})
        SS.init_schema()

    def tearDown(self):
        AS.save_automation_settings({"paused": "1"})

    def test_legacy_schedule_becomes_first_job_once(self):
        jobs = SS.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0]["frequency"], jobs[0]["times"], jobs[0]["weekdays"]), ("weekly", "10:00,15:00", "1111100"))
        SS.delete_job(jobs[0]["id"])
        SS.init_schema()                       # deleting every job must not bring it back
        self.assertEqual(SS.list_jobs(), [])

    def test_weekly_grace_and_no_double_fire(self):
        mon_1010 = datetime(2026, 10, 26, 10, 10)
        due, _ = scheduler.due_jobs(mon_1010)
        self.assertEqual([t for _, t in due], ["10:00"])
        self.assertEqual(scheduler.due_jobs(datetime(2026, 10, 26, 9, 59))[0], [])
        self.assertEqual(scheduler.due_jobs(datetime(2026, 10, 26, 10, 45))[0], [])     # past the 45 min grace
        self.assertEqual(scheduler.due_jobs(datetime(2026, 10, 31, 10, 5))[0], [])      # Saturday off
        with mock.patch.object(scheduler, "_run_job", return_value={"ok": True, "status": "success", "message": "ok"}) as run:
            r = scheduler.run_due(now=mon_1010)
            self.assertEqual(run.call_count, 1)
            self.assertTrue(r["ran"])
            r = scheduler.run_due(now=mon_1010)               # same slot again (other trigger)
            self.assertEqual(run.call_count, 1)
        self.assertFalse(SS.record_fire(SS.list_jobs()[0]["id"], "2026-10-26", "10:00", "scheduled"))
        self.assertTrue(SS.last_fires()[0]["outcome"].startswith("success"))

    def test_pause_skip_and_holidays(self):
        mon_1010 = datetime(2026, 10, 26, 10, 10)
        AS.save_automation_settings({"skip_date": "2026-10-26"})
        self.assertEqual(scheduler.due_jobs(mon_1010), ([], "Skipping today."))
        AS.save_automation_settings({"skip_date": "", "paused": "1"})
        self.assertEqual(scheduler.due_jobs(mon_1010), ([], "Automation is paused."))
        self.assertIsNone(scheduler.next_run(mon_1010))
        AS.save_automation_settings({"paused": "0"})
        n, problems = SS.add_holidays("2026-10-26 Test Poya\n27/10/2026, Second\nnonsense")
        self.assertEqual((n, len(problems)), (2, 1))
        self.assertEqual(scheduler.due_jobs(mon_1010), ([], "Holiday: Test Poya."))
        self.assertEqual(scheduler.next_run(mon_1010), datetime(2026, 10, 28, 10, 0))   # 26 + 27 are holidays
        j = SS.list_jobs()[0]
        SS.save_job(dict(j, times=j["times_list"], skip_holidays=False))
        self.assertEqual(len(scheduler.due_jobs(mon_1010)[0]), 1)                        # this job runs on holidays

    def test_switched_off_type_doesnt_run(self):
        mon_1010 = datetime(2026, 10, 26, 10, 10)
        AS.save_automation_settings({"auto_sfa_downloads": "0"})
        try:
            self.assertEqual(scheduler.due_jobs(mon_1010)[0], [])
            self.assertIsNone(scheduler.next_run(mon_1010))
        finally:
            AS.save_automation_settings({"auto_sfa_downloads": "1"})
        self.assertEqual(len(scheduler.due_jobs(mon_1010)[0]), 1)

    def test_monthly_days_and_last_day(self):
        SS.delete_job(SS.list_jobs()[0]["id"])
        job(name="Month", frequency="monthly", month_days="1,31", times="06:00")
        job(name="Month end", frequency="monthly", month_days="last", times="20:00")
        runs = [o["at"] for o in SS.occurrences(datetime(2026, 11, 1), datetime(2027, 3, 1))]
        self.assertIn(datetime(2026, 11, 30, 6, 0), runs)      # 31 -> 30 Nov
        self.assertIn(datetime(2027, 2, 28, 6, 0), runs)       # 31 -> 28 Feb
        self.assertIn(datetime(2026, 12, 31, 20, 0), runs)
        self.assertIn(datetime(2027, 1, 1, 6, 0), runs)
        self.assertNotIn(datetime(2026, 11, 15, 6, 0), runs)
        self.assertEqual(scheduler.next_run(datetime(2026, 11, 2)), datetime(2026, 11, 30, 6, 0))

    def test_once(self):
        SS.delete_job(SS.list_jobs()[0]["id"])
        d = (date.today() + timedelta(days=3)).isoformat()
        job(name="One off", frequency="once", once_date=d, times="14:00")
        nxt = scheduler.next_run()
        self.assertEqual(nxt, datetime.fromisoformat(d + "T14:00"))
        ok, msg, _ = SS.save_job({"name": "Past", "frequency": "once", "once_date": "2020-01-01", "times": "10:00"})
        self.assertFalse(ok)

    def test_validation(self):
        bad = [{"name": ""}, {"times": "25:00"}, {"times": ""}, {"frequency": "weekly", "weekdays": "0000000"},
               {"frequency": "monthly", "month_days": "32"}, {"job_type": "master_sync"}, {"job_type": "nope"},
               {"options": {"report_day": "tomorrow"}}]
        for b in bad:
            ok, _, _ = SS.save_job(dict({"name": "x", "frequency": "daily", "times": "10:00"}, **b))
            self.assertFalse(ok, b)
        self.assertEqual(SS.describe(job(frequency="monthly", month_days="last,2,1"))
                         , "Monthly on the 1st, 2nd, last day at 10:00")

    def test_yesterday_report_date(self):
        j = job(name="Catch-up", options={"report_day": "yesterday"})
        import sfa_automation
        with mock.patch.object(sfa_automation, "run_automation", return_value={"ok": True, "status": "success"}) as ra:
            scheduler._run_job(SS.get_job(j["id"]), "10:00", "scheduled")
        self.assertEqual(ra.call_args.kwargs["report_date"], (date.today() - timedelta(days=1)).isoformat())

    def test_task_times_cover_all_jobs(self):
        job(name="Morning", frequency="monthly", month_days="1", times="06:00,07:30")
        job(name="Off", times="23:00", enabled=False)
        self.assertEqual(scheduler.wanted_task_times(), ["06:00", "07:30", "10:00", "15:00"])

    def test_clock(self):
        self.assertIsNone(scheduler.record_clock("not a date", "x"))
        server = (datetime.now().astimezone() + timedelta(minutes=7)).strftime("%a, %d %b %Y %H:%M:%S %z")
        off = scheduler.record_clock(server, "test")
        self.assertAlmostEqual(off, 420, delta=3)
        st = scheduler.clock_status()
        self.assertEqual(st["level"], "bad")
        self.assertIn("behind", st["message"])
        scheduler.record_clock(datetime.now().astimezone().strftime("%a, %d %b %Y %H:%M:%S %z"), "test")
        self.assertEqual(scheduler.clock_status()["level"], "ok")


if __name__ == "__main__":
    unittest.main()
