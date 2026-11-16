"""
Splash screen startup steps (startup.py): the bar follows real steps, SFA being
unreachable never blocks, itineraries are read only when due, and the full sync
is remembered per day. No browser: the SFA check and the itinerary read are faked.

Run:  py -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

_TMP_HOME = tempfile.mkdtemp()
os.environ["HOME"] = _TMP_HOME
os.environ["USERPROFILE"] = _TMP_HOME

import db  # noqa: E402
db.APP_DATA_DIR = os.path.join(_TMP_HOME, "Chimera", "data")
db.DB_PATH = os.path.join(db.APP_DATA_DIR, "chimera.db")
import automation_store as AS  # noqa: E402
import outlet_store as OS  # noqa: E402
import itinerary_automation as IA  # noqa: E402
import startup  # noqa: E402


class StartupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        OS.init_schema()

    def setUp(self):
        self.keep = startup.check_sfa, IA.run_read
        conn = db.get_connection()
        conn.execute("DELETE FROM ou_territories")
        conn.commit()
        conn.close()
        AS.save_automation_settings({"startup_full_day": "", "auto_outlet_updates": "1", "paused": "0"})

    def tearDown(self):
        startup.check_sfa, IA.run_read = self.keep

    def test_offline_never_blocks(self):
        startup.check_sfa = lambda: (False, "No internet connection, or SFA can't be reached")
        startup.run()
        s = startup.snapshot()
        self.assertTrue(s["done"])
        self.assertEqual(s["percent"], 100)
        self.assertEqual(s["sfa"], "offline")
        st = {x["id"]: x["status"] for x in s["steps"]}
        self.assertEqual(st, {"start": "done", "sfa": "failed", "itinerary": "skipped"})
        self.assertFalse(AS.get_automation_settings()["startup_full_day"])     # not counted as the day's full sync

    def test_reads_due_itineraries_and_remembers_the_day(self):
        startup.check_sfa = lambda: (True, "Connected")
        for t in ("Alpha Town", "Beta Bay"):
            OS.add_territory(t)
        asked = []

        def fake_read(territories=None, months=None, trigger="manual", headless=None):
            asked.append((sorted(territories), trigger))
            IA.LIVE.reset(kind="itinerary", trigger=trigger, lanes=[{"name": t, "status": "done"} for t in territories])
            time.sleep(0.2)
            res = {"ok": True, "status": "done", "message": f"{len(territories)} territories read."}
            IA.LIVE.set(active=False, phase="done", result=res)
            return res
        IA.run_read = fake_read
        startup.run()
        s = startup.snapshot()
        self.assertEqual(asked, [(["Alpha Town", "Beta Bay"], "startup")])
        it = next(x for x in s["steps"] if x["id"] == "itinerary")
        self.assertEqual(it["status"], "done")
        self.assertIn("territories read", it["message"])
        self.assertEqual(s["sfa"], "connected")
        self.assertEqual(AS.get_automation_settings()["startup_full_day"], time.strftime("%Y-%m-%d"))
        self.assertFalse(startup.is_first_open_today())

    def test_skips_itinerary_when_automation_off(self):
        startup.check_sfa = lambda: (True, "Connected")
        AS.save_automation_settings({"auto_outlet_updates": "0"})
        IA.run_read = lambda *a, **k: self.fail("must not read")
        startup.run()
        it = next(x for x in startup.snapshot()["steps"] if x["id"] == "itinerary")
        self.assertEqual(it["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
