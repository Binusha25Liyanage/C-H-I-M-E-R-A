"""
Tests for WhatsApp reminders (sent N minutes before each report time, or at
times you set, to their own chats) and the Running now overview. No
browser and no WhatsApp: the plan, the skip rules, the once-a-day record,
and the words.

Run:  py -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime

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
import reminders as R  # noqa: E402

DAY = date(2026, 11, 12)          # a Thursday


def at(hm):
    h, m = (int(x) for x in hm.split(":"))
    return datetime(DAY.year, DAY.month, DAY.day, h, m)


class RemindersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        SS.init_schema()
        R.init_schema()

    def setUp(self):
        conn = db.get_connection()
        for t in ("wa_reminders", "wa_reminder_sends", "schedule_jobs", "holidays"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()
        AS.save_automation_settings({"paused": "0", "skip_date": "", "auto_sfa_downloads": "1"})
        ok, msg, job = SS.save_job({"name": "Daily Sales Reports", "job_type": "sfa_downloads", "frequency": "daily",
                                    "times": ["10:15", "12:15", "14:15", "16:15"], "enabled": True})
        self.assertTrue(ok, msg)
        self.job = job if isinstance(job, dict) else SS.list_jobs()[0]

    def add(self, **kw):
        data = {"name": "Sync your bills", "text": R.DEFAULT_TEXT, "job_id": self.job["id"], "offset_min": 15,
                "extra_times": [], "chats": ["CEN / UVA / SUB TEAM"], "enabled": True}
        data.update(kw)
        ok, msg, rid = R.save_reminder(data)
        self.assertTrue(ok, msg)
        self.active_from(rid, "2026-11-12T00:00:00")
        return rid

    def active_from(self, rid, when):
        conn = db.get_connection()
        conn.execute("UPDATE wa_reminders SET active_from=? WHERE id=?", (when, rid))
        conn.commit()
        conn.close()

    def test_words(self):
        self.assertEqual([R.fmt_time(x) for x in ("12:15", "14:15", "09:05", "00:30", "")],
                         ["12.15 PM", "2.15 PM", "9.05 AM", "12.30 AM", ""])
        txt = R.render(R.DEFAULT_TEXT, "12:15", at("12:00"))
        self.assertEqual(txt, "❗ Please sync your bills for the *12.15 PM Sales Report Live Update* as soon as possible "
                              "to keep the report accurate and up to date.")
        self.assertEqual(R.render("{day} {date}", "", at("08:00")), "Thursday 2026-11-12")

    def test_validation(self):
        self.assertFalse(R.save_reminder({"text": "", "chats": ["A"], "job_id": self.job["id"]})[0])
        self.assertFalse(R.save_reminder({"text": "x", "chats": [], "job_id": self.job["id"]})[0])
        self.assertFalse(R.save_reminder({"text": "x", "chats": ["A"]})[0])                      # no job, no times
        self.assertFalse(R.save_reminder({"text": "x", "chats": ["A"], "extra_times": ["25:00"]})[0])
        self.assertFalse(R.save_reminder({"text": "x", "chats": ["A"], "job_id": self.job["id"], "offset_min": 999})[0])
        ok, _m, rid = R.save_reminder({"text": "x", "chats": ["A", "A", " B "], "extra_times": "9:5, 11:55"})
        r = R.get_reminder(rid)
        self.assertEqual((r["chats"], r["extra_list"]), (["A", "B"], ["09:05", "11:55"]))

    def test_plan_follows_the_job(self):
        self.add(extra_times=["11:50"])
        plan = R.plan_day(DAY)
        self.assertEqual([(p["slot"], p["report_time"]) for p in plan],
                         [("10:00", "10:15"), ("11:50", "12:15"), ("12:00", "12:15"), ("14:00", "14:15"), ("16:00", "16:15")])
        self.assertTrue(all(p["skip"] is None for p in plan))
        SS.save_job({**self.job, "times": ["12:15", "15:45"]})                     # the job's times change …
        self.assertEqual([p["slot"] for p in R.plan_day(DAY)], ["11:50", "12:00", "15:30"])   # … the reminders follow
        self.assertIn("15:30", R.task_times())                                      # Windows wakes Chimera for it

    def test_skipped_when_the_run_wont_happen(self):
        self.add()
        AS.save_automation_settings({"paused": "1"})
        self.assertEqual({p["skip"] for p in R.plan_day(DAY)}, {"automation is paused"})
        AS.save_automation_settings({"paused": "0", "auto_sfa_downloads": "0"})
        self.assertIn("switched off", R.plan_day(DAY)[0]["skip"])
        AS.save_automation_settings({"auto_sfa_downloads": "1"})
        SS.save_job({**self.job, "frequency": "weekly", "weekdays": "1111000"})    # Mon-Thu… then Friday:
        self.assertIsNone(R.plan_day(DAY)[0]["skip"])
        self.assertIn("doesn't run on Fridays", R.plan_day(date(2026, 11, 13))[0]["skip"])
        SS.save_job({**self.job, "frequency": "daily", "weekdays": "1111111"})
        SS.add_holidays("2026-11-12 Test holiday")
        self.assertIn("holiday", R.plan_day(DAY)[0]["skip"])
        R.set_enabled(R.list_reminders()[0]["id"], False)
        self.assertEqual(R.plan_day(DAY), [])

    def test_due_sends_once_and_records(self):
        rid = self.add()
        sent = []
        orig = R.send_items
        R.send_items = lambda items, wait=True, test=False: [sent.append(i["slot"]) or R._record(i, "sent", [], "", "ok")
                                                               for i in items] and {"sent": len(items), "busy": False}
        try:
            self.assertEqual(R.run_due(at("09:59"))["sent"], 0)
            self.assertEqual(R.run_due(at("10:00"))["sent"], 1)
            self.assertEqual(R.run_due(at("10:05"))["sent"], 0)                     # never twice
            self.assertEqual(R.run_due(at("12:20"))["missed"], 1)                   # Chimera was off past the report
            AS.save_automation_settings({"paused": "1"})
            self.assertEqual(R.run_due(at("14:01"))["skipped"], 1)
        finally:
            R.send_items = orig
            AS.save_automation_settings({"paused": "0"})
        self.assertEqual(sent, ["10:00"])
        st = {(s["send_at"], s["status"]) for s in R.recent_sends()}
        self.assertEqual(st, {("10:00", "sent"), ("12:00", "skipped"), ("14:00", "skipped")})
        import activity
        ev = [e for e in activity.events(DAY.isoformat(), DAY.isoformat()) if e["process"] == "whatsapp"]
        self.assertTrue(any(e["title"].startswith("Reminder skipped") for e in ev))
        self.assertTrue(any("12.15 PM report" in e["title"] for e in ev))
        self.assertTrue(R.plan_day(DAY)[0]["done"] == "sent")

    def test_times_before_it_existed_dont_count(self):
        rid = self.add()
        self.active_from(rid, "2026-11-12T12:05:00")          # saved at 12:05: 10:00 and 12:00 never happened for it
        orig = R.send_items
        R.send_items = lambda items, wait=True, test=False: {"sent": len(items), "busy": False}
        try:
            r = R.run_due(at("13:00"))
        finally:
            R.send_items = orig
        self.assertEqual((r["sent"], r["skipped"], r["missed"]), (0, 0, 0))
        self.assertEqual(R.recent_sends(), [])
        self.assertEqual(R.due(at("14:00"))[0][0]["slot"], "14:00")

    def test_busy_whatsapp_tries_again(self):
        self.add()
        orig = R.send_items
        calls = []
        R.send_items = lambda items, wait=True, test=False: calls.append(1) or {"sent": 0, "busy": True}
        try:
            self.assertTrue(R.run_due(at("10:00"))["busy"])
            self.assertTrue(R.run_due(at("10:01"))["busy"])                         # still due: tried again
        finally:
            R.send_items = orig
        self.assertEqual(len(calls), 2)

    def test_running_now_overview(self):
        import running_now
        self.add()
        ov = running_now.overview()
        self.assertEqual({p["id"] for p in ov["running"]}, {"reports", "whatsapp", "loadings", "itinerary", "outlets"})
        self.assertEqual(ov["active_count"], 0)
        self.assertIn("coming_up", ov)
        self.assertFalse(running_now.stop("nonsense")["ok"])
        self.assertTrue(running_now.stop("outlets")["ok"])


if __name__ == "__main__":
    unittest.main()
