"""
Multitasking: different kinds of SFA job run side by side, the same kind queues instead of being missed,
each kind has its own lock, and slots that never ran are marked Missed. No browser needed.

Run:  py -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
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
AS.LOCK_PATH = os.path.join(db.APP_DATA_DIR, "automation.lock")
import schedule_store as SS  # noqa: E402
import scheduler  # noqa: E402
import lanes  # noqa: E402


def job(**kw):
    base = {"name": "Job", "job_type": "sfa_downloads", "frequency": "daily", "times": "10:00",
            "enabled": True, "skip_holidays": False, "options": {"report_day": "today"}}
    base.update(kw)
    ok, msg, j = SS.save_job(base)
    assert ok, msg
    return j


class FakeLive:
    def __init__(self, active=False):
        self.active = active

    def snapshot(self):
        return {"active": self.active}


class SideBySideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        SS.init_schema()
        scheduler.QUEUE_POLL_S = 0.05

    def setUp(self):
        conn = db.get_connection()
        for t in ("schedule_jobs", "schedule_fires"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()
        AS.save_automation_settings({"paused": "0", "skip_date": "", "side_by_side": "1",
                                     "auto_sfa_downloads": "1", "auto_rep_attendance": "1", "auto_loadings": "1",
                                     "auto_distributor_sync": "1"})
        AS.release_lock(lane="*")

    def tearDown(self):
        AS.save_automation_settings({"paused": "1", "side_by_side": "1"})
        AS.release_lock(lane="*")

    # ---- locks
    def test_each_kind_has_its_own_lock(self):
        self.assertTrue(AS.acquire_lock("reports run", "reports"))
        self.assertTrue(AS.acquire_lock("loadings run", "loadings"))      # side by side: both at once
        self.assertFalse(AS.acquire_lock("second loadings", "loadings"))  # same kind: no
        self.assertEqual(AS.lock_holder("loadings")["owner"], "loadings run")
        self.assertEqual(len(AS.lock_holders()), 2)
        AS.release_lock(lane="loadings")
        self.assertIsNotNone(AS.lock_holder("reports"))                  # releasing one leaves the other
        AS.release_lock(lane="*")
        self.assertEqual(AS.lock_holders(), [])

    def test_side_by_side_off_means_one_lock(self):
        AS.save_automation_settings({"side_by_side": "0"})
        self.assertTrue(AS.acquire_lock("reports run", "reports"))
        self.assertFalse(AS.acquire_lock("attendance", "attendance"))

    # ---- who may start
    def test_busy_message_per_lane(self):
        fakes = {"sfa_automation": FakeLive(True), "loading_automation": FakeLive(), "rep_attendance_automation": FakeLive(),
                 "itinerary_automation": FakeLive(), "sequence_automation": FakeLive()}
        with mock.patch("importlib.import_module", side_effect=lambda m: mock.Mock(LIVE=fakes[m])):
            self.assertIsNotNone(lanes.busy_message("reports"))
            self.assertIsNone(lanes.busy_message("attendance"))           # a report download doesn't block attendance
            AS.save_automation_settings({"side_by_side": "0"})
            self.assertIn("side by side", lanes.busy_message("attendance"))

    def test_job_lanes(self):
        self.assertEqual(lanes.job_lane("report_early"), "reports")
        self.assertEqual(lanes.job_lane("distributors"), "loadings")
        self.assertEqual(lanes.job_lane("rep_attendance"), "attendance")

    # ---- scheduler
    def _run(self, jobs, now, busy=None):
        """Run the due jobs with a fake _run_job that takes 0.3 s; returns [(name, start, end)]."""
        spans, lock = [], threading.Lock()

        def fake_run(j, slot, trigger):
            t0 = time.time()
            time.sleep(0.3)
            with lock:
                spans.append((j["name"], t0, time.time()))
            return {"ok": True, "status": "success", "message": "done"}

        with mock.patch.object(scheduler, "_run_job", fake_run), \
                mock.patch.object(lanes, "busy_message", busy or (lambda lane: None)):
            scheduler.run_due("scheduled", now=now)
        return spans

    def test_different_kinds_run_together_same_kind_queues(self):
        a = job(name="Reports A", job_type="sfa_downloads")
        b = job(name="Attendance", job_type="rep_attendance")
        c = job(name="Reports B", job_type="report_sku")
        spans = {n: (s, e) for n, s, e in self._run([a, b, c], datetime.now().replace(hour=10, minute=5))}
        self.assertEqual(set(spans), {"Reports A", "Attendance", "Reports B"})
        ra, at, rb = spans["Reports A"], spans["Attendance"], spans["Reports B"]
        self.assertLess(at[0], ra[1])                       # attendance started while report A was running
        first, second = sorted([ra, rb])
        self.assertGreaterEqual(second[0], first[1] - 0.01)  # the two report jobs one after the other

    def test_side_by_side_off_runs_one_at_a_time(self):
        AS.save_automation_settings({"side_by_side": "0"})
        job(name="Reports A", job_type="sfa_downloads")
        job(name="Attendance", job_type="rep_attendance")
        spans = sorted(self._run(None, datetime.now().replace(hour=10, minute=5)), key=lambda x: x[1])
        self.assertGreaterEqual(spans[1][1], spans[0][2] - 0.01)

    def test_busy_kind_waits_in_queue_instead_of_being_missed(self):
        j = job(name="Attendance", job_type="rep_attendance")
        state = {"until": time.time() + 0.4}
        busy = lambda lane: "Another rep attendance run is already in progress." if time.time() < state["until"] else None
        seen = []
        orig = SS.set_fire_outcome
        with mock.patch.object(SS, "set_fire_outcome", lambda *a: (seen.append(a[3]), orig(*a))):
            spans = self._run(None, datetime.now().replace(hour=10, minute=5), busy=busy)
        self.assertEqual(len(spans), 1)                      # it ran, after waiting
        self.assertTrue(any(o.startswith("queued: waiting") for o in seen))
        self.assertTrue(SS.last_fires(5)[0]["outcome"].startswith("success"))

    # ---- missed
    def test_slots_that_never_ran_are_marked_missed(self):
        j = job(name="Early", job_type="sfa_downloads", times="08:00,18:00")
        conn = db.get_connection()                           # the job existed before today
        conn.execute("UPDATE schedule_jobs SET created_at='2020-01-01T00:00:00', updated_at='2020-01-01T00:00:00'")
        conn.commit()
        conn.close()
        with mock.patch.object(scheduler, "_notify_missed") as note:
            marked = scheduler.mark_missed(datetime.now().replace(hour=12, minute=0))
        self.assertEqual(marked, [(j["id"], "08:00")])       # 18:00 hasn't come yet
        self.assertTrue(SS.last_fires(5)[0]["outcome"].startswith("missed:"))
        note.assert_called_once()
        self.assertEqual(scheduler.mark_missed(datetime.now().replace(hour=12, minute=1)), [])   # only once

    def test_job_left_in_a_closed_chimeras_queue_is_missed(self):
        j = job(name="Attendance", job_type="rep_attendance")
        today = datetime.now().date().isoformat()
        SS.record_fire(j["id"], today, "10:00", "scheduled")
        SS.set_fire_outcome(j["id"], today, "10:00", "queued: waiting — busy [pid 999999]")
        with mock.patch.object(scheduler, "_notify_missed"), mock.patch.object(AS, "pid_alive", return_value=False):
            scheduler.mark_missed(datetime.now().replace(hour=10, minute=10))
        self.assertTrue(SS.last_fires(5)[0]["outcome"].startswith("missed: Chimera was closed"))


if __name__ == "__main__":
    unittest.main()
