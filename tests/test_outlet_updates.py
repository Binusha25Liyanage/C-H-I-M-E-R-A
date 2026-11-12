"""
Tests for Outlet Updates step 1: which day is being prepared (23:59 rule and
catch-up when the PC was off), when an itinerary needs reading again, the
day states on the routes board, and the checks on a calendar reading.
No browser needed.

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
import outlet_store as OS  # noqa: E402
import itinerary_automation as IA  # noqa: E402


def nov_days(no_route_day=14):
    days = {}
    for d in range(1, 31):
        iso = f"2026-11-{d:02d}"
        if date(2026, 11, d).weekday() == 6:
            days[iso] = ("H", "")
        elif d == no_route_day:
            days[iso] = ("WD", "-")
        else:
            days[iso] = ("WD", f"Route {d}")
    return days


class OutletUpdatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        OS.init_schema()

    def setUp(self):
        conn = db.get_connection()
        for t in ("ou_territories", "ou_itinerary_months", "ou_itinerary_days", "ou_runs"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()
        OS.merge_detected(["Galnewa", "Chilaw", "Galle"])
        OS.set_enabled(["Galnewa", "Chilaw"], True)

    def test_target_day_switches_at_prepare_time(self):
        self.assertEqual(OS.target_day(datetime(2026, 11, 3, 23, 58)), date(2026, 11, 3))
        self.assertEqual(OS.target_day(datetime(2026, 11, 3, 23, 59)), date(2026, 11, 4))
        self.assertEqual(OS.target_day(datetime(2026, 11, 4, 7, 30)), date(2026, 11, 4))   # PC was off at midnight
        self.assertEqual(OS.target_day(datetime(2026, 11, 30, 23, 59)), date(2026, 12, 1))  # next month
        self.assertEqual(OS.target_day(datetime(2026, 11, 3, 22, 30), "22:30"), date(2026, 11, 4))

    def test_prepare_boundary(self):
        self.assertEqual(OS.prepare_boundary(datetime(2026, 11, 4, 7, 30)), datetime(2026, 11, 3, 23, 59))
        self.assertEqual(OS.prepare_boundary(datetime(2026, 11, 3, 23, 59, 30)), datetime(2026, 11, 3, 23, 59))

    def test_detect_keeps_choices_and_flags_gone(self):
        added = OS.merge_detected(["Galnewa", "Kekirawa"])
        self.assertEqual(added, ["Kekirawa"])
        t = {x["territory"]: x for x in OS.list_territories()}
        self.assertTrue(t["Galnewa"]["enabled"])
        self.assertFalse(t["Kekirawa"]["enabled"])            # new ones start switched off
        self.assertFalse(t["Chilaw"]["seen_in_sfa"])          # no longer in SFA's list

    def test_due_and_catch_up(self):
        now = datetime(2026, 11, 4, 7, 30)
        day, due = OS.due_territories(now)
        self.assertEqual((day, sorted(due)), (date(2026, 11, 4), ["Chilaw", "Galnewa"]))
        with mock.patch.object(OS, "_now", return_value="2026-11-04T07:31:00"):
            OS.save_month("Galnewa", "2026-11", "ok", nov_days(), rep="CSR Galnewa - Nadeera")
            OS.save_month("Chilaw", "2026-11", "ok", nov_days())
        self.assertEqual(OS.due_territories(datetime(2026, 11, 4, 9, 0))[1], [])          # done for today
        self.assertEqual(sorted(OS.due_territories(datetime(2026, 11, 4, 23, 59, 10))[1]),  # nightly re-read
                         ["Chilaw", "Galnewa"])
        self.assertEqual(OS.get_territory("Galnewa")["rep"], "CSR Galnewa - Nadeera")

    def test_failed_read_keeps_calendar_and_retries_later(self):
        with mock.patch.object(OS, "_now", return_value="2026-11-04T07:31:00"):
            OS.save_month("Galnewa", "2026-11", "ok", nov_days())
            OS.save_month("Chilaw", "2026-11", "ok", nov_days())
        with mock.patch.object(OS, "_now", return_value="2026-11-04T23:59:20"):
            OS.save_month("Galnewa", "2026-11", "failed", message="SFA timed out")
        self.assertEqual(OS.day_for("Galnewa", "2026-11-05")["route"], "Route 5")          # old calendar kept
        self.assertNotIn("Galnewa", OS.due_territories(datetime(2026, 11, 5, 0, 5))[1])     # not retried at once
        self.assertIn("Galnewa", OS.due_territories(datetime(2026, 11, 5, 0, 20))[1])       # retried after 15 min

    def test_missing_month_is_checked_again_hourly(self):
        with mock.patch.object(OS, "_now", return_value="2026-11-30T23:59:30"):
            OS.save_month("Chilaw", "2026-12", "missing", message="SFA has no December 2026 itinerary for Chilaw yet.")
        self.assertEqual(OS.day_for("Chilaw", "2026-12-01")["state"], "missing")
        self.assertNotIn("Chilaw", OS.due_territories(datetime(2026, 12, 1, 0, 30))[1])
        self.assertIn("Chilaw", OS.due_territories(datetime(2026, 12, 1, 1, 5))[1])

    def test_board_states(self):
        OS.save_month("Galnewa", "2026-11", "ok", nov_days())
        states = {d: OS.day_for("Galnewa", d)["state"] for d in ("2026-11-04", "2026-11-08", "2026-11-14")}
        self.assertEqual(states, {"2026-11-04": "route", "2026-11-08": "holiday", "2026-11-14": "no_route"})
        self.assertEqual(OS.day_for("Chilaw", "2026-11-04")["state"], "not_read")
        board = {b["territory"]: b for b in OS.board("2026-11-04")}
        self.assertEqual(set(board), {"Galnewa", "Chilaw"})                                 # only working ones
        self.assertEqual(board["Galnewa"]["route"], "Route 4")

    def test_parse_calendar(self):
        raw = {"header": {"year": "2026", "month": "NOVEMBER", "rep": "CSR Galnewa - Nadeera"},
               "days": [{"day": d, "badge": b, "route": r} for d, (b, r) in
                        ((int(k[-2:]), v) for k, v in nov_days().items())]}
        days = IA.parse_calendar(raw, 2026, 11)
        self.assertEqual(len(days), 30)
        self.assertEqual(days["2026-11-01"], ("H", ""))
        with self.assertRaises(IA.StepError):                     # wrong month opened
            IA.parse_calendar({**raw, "header": {**raw["header"], "month": "OCTOBER"}}, 2026, 11)
        with self.assertRaises(IA.StepError):                     # days missing from the page
            IA.parse_calendar({**raw, "days": raw["days"][:20]}, 2026, 11)

    def test_shared_itinerary_is_not_used(self):
        seen = {("2026-11", "https://x/it/month-itinerary/3738/false"): ["Chilaw", "Colombo SMMT"],
                ("2026-11", "https://x/it/month-itinerary/3740/false"): ["Galnewa"]}
        self.assertEqual(list(IA._duplicates(seen).values()), [["Chilaw", "Colombo SMMT"]])
        OS.save_month("Chilaw", "2026-11", "ok", nov_days(), rep="CSR Chilaw - Dimantha")
        OS.save_month("Chilaw", "2026-11", "failed", message="same itinerary", clear=True)
        self.assertEqual(OS.day_for("Chilaw", "2026-11-05")["state"], "failed")        # wrong calendar thrown away
        self.assertEqual(OS.get_territory("Chilaw")["rep"], "")

    def test_upgrade_rereads_old_itineraries(self):
        OS.save_month("Galnewa", "2026-11", "ok", nov_days(), rep="CSR Chilaw - Dimantha")
        conn = db.get_connection()
        conn.execute("UPDATE ou_meta SET value='1' WHERE key='reader_version'")
        conn.commit()
        conn.close()
        OS.init_schema()
        self.assertEqual(OS.day_for("Galnewa", "2026-11-05")["state"], "not_read")
        self.assertEqual(OS.get_territory("Galnewa")["rep"], "")
        self.assertTrue(OS.get_territory("Galnewa")["enabled"])                            # choices kept
        OS.save_month("Galnewa", "2026-11", "ok", nov_days())
        OS.init_schema()                                                                    # only once
        self.assertEqual(OS.day_for("Galnewa", "2026-11-05")["state"], "route")

    def test_settings_validation(self):
        self.assertFalse(AS.save_automation_settings({"ou_prepare_time": "25:00"})[0])
        self.assertFalse(AS.save_automation_settings({"ou_tabs": "40"})[0])
        self.assertFalse(AS.save_automation_settings({"ou_itinerary_url": "lakmee.salespro.lk"})[0])
        self.assertTrue(AS.save_automation_settings({"ou_prepare_time": "0:30"})[0])
        self.assertEqual(AS.get_automation_settings()["ou_prepare_time"], "00:30")
        AS.save_automation_settings({"ou_prepare_time": "23:59"})

    def test_sheet_link_must_be_google(self):
        self.assertFalse(OS.update_territory("Galnewa", {"sheet_url": "https://example.com/x"})[0])
        self.assertTrue(OS.update_territory("Galnewa", {"manager": "W. M. Bandara"})[0])


if __name__ == "__main__":
    unittest.main()
