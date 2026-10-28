"""
Tests for the SFA automation building blocks that don't need a browser:
report-profile validation, automation settings, the scheduler's
due-slot rules, the run lock, and file validation against a profile.

Run:  py -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)

_TMP_HOME = tempfile.mkdtemp()
os.environ["HOME"] = _TMP_HOME
os.environ["USERPROFILE"] = _TMP_HOME

import db  # noqa: E402
db.APP_DATA_DIR = os.path.join(_TMP_HOME, "Chimera", "data")
db.DB_PATH = os.path.join(db.APP_DATA_DIR, "chimera.db")
import automation_store as AS  # noqa: E402
AS.LOCK_PATH = os.path.join(db.APP_DATA_DIR, "automation.lock")
import sfa_store  # noqa: E402
import report_pipeline  # noqa: E402
import scheduler  # noqa: E402
import sfa_automation as E  # noqa: E402
from test_daily_reports import raw_export, SKU_H, sku  # noqa: E402

D = "2026-10-26"


class AutomationStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        sfa_store.init_schema()
        report_pipeline.init_schema()
        AS.init_schema()

    def test_seed_profiles(self):
        names = [p["feeds_into"] for p in AS.list_profiles()]
        for k in ("invoices", "sales_orders", "sku_van", "sku_pre", "unproductive"):
            self.assertIn(k, names)
        sku_pre = next(p for p in AS.list_profiles() if p["feeds_into"] == "sku_pre")
        self.assertEqual(sku_pre["filters"][0]["value"], "Pre Sales Order - Pending Only")
        missing_url = next(p for p in AS.list_profiles() if p["feeds_into"] == "invoices")
        self.assertIn("Navigation URL is not set.", missing_url["problems"])

    def test_profile_validation(self):
        bad = [({"name": ""}, "name"), ({"name": "x", "url": "ftp://x"}, "http"),
               ({"name": "x", "feeds_into": "nope"}, "feeds"),
               ({"name": "x", "filters": [{"label": "", "type": "dropdown"}]}, "label"),
               ({"name": "x", "filters": [{"label": "A", "type": "dropdown"}, {"label": "a", "type": "text"}]}, "twice"),
               ({"name": "x", "filters": [{"label": "D", "type": "date", "date_mode": "fixed", "value": ""}]}, "fixed"),
               ({"name": "x", "wait_seconds": 1}, "between")]
        for data, word in bad:
            ok, msg, _ = AS.save_profile(data)
            self.assertFalse(ok, data)
            self.assertIn(word, msg.lower())

    def test_profile_crud(self):
        ok, msg, p = AS.save_profile({
            "name": "Test report", "url": "https://example.com/r", "feeds_into": "download_only",
            "filters": [{"label": "Territory", "type": "multiselect", "value": "Galle, Matara"},
                        {"label": "Show Filters", "type": "toggle", "value": "true"},
                        {"label": "Date From", "type": "date", "date_mode": "yesterday"}]})
        self.assertTrue(ok, msg)
        self.assertEqual(p["filters"][0]["value"], ["Galle", "Matara"])
        self.assertEqual(p["filters"][1]["value"], "on")
        ok, _, copy = AS.duplicate_profile(p["id"])
        self.assertTrue(ok)
        self.assertFalse(copy["enabled"])
        self.assertTrue(copy["name"].endswith("(copy)"))
        ids = [x["id"] for x in AS.list_profiles()]
        AS.reorder_profiles(list(reversed(ids)))
        self.assertEqual(AS.list_profiles()[0]["id"], ids[-1])
        self.assertTrue(AS.delete_profile(copy["id"]))
        self.assertTrue(AS.delete_profile(p["id"]))

    def test_resolve_date(self):
        self.assertEqual(AS.resolve_date({"date_mode": "report_date"}, D), D)
        self.assertEqual(AS.resolve_date({"date_mode": "fixed", "value": "2026-01-02"}, D), "2026-01-02")

    def test_settings_validation(self):
        ok, _ = AS.save_automation_settings({"parallel_tabs": "12"})
        self.assertTrue(ok)
        for bad in ({"run_times": "25:00"}, {"weekdays": "11"}, {"parallel_tabs": "13"}, {"parallel_tabs": "0"}, {"retries": "x"},
                    {"skip_date": "26/10"}, {"generate_kinds": "sku,nope"}):
            ok, _ = AS.save_automation_settings(bad)
            self.assertFalse(ok, bad)
        ok, _ = AS.save_automation_settings({"run_times": "17:30, 9:05,09:05"})
        self.assertTrue(ok)
        self.assertEqual(AS.get_automation_settings()["run_times_list"], ["09:05", "17:30"])

    def test_lock_left_by_closed_app_is_stale(self):
        import json as _j
        AS.release_lock()
        os.makedirs(os.path.dirname(AS.LOCK_PATH), exist_ok=True)
        with open(AS.LOCK_PATH, "w") as f:
            _j.dump({"owner": "closed window", "pid": 999999, "since": __import__("time").time()}, f)
        self.assertFalse(AS.pid_alive(999999))
        self.assertTrue(AS.acquire_lock("new run"))          # dead process -> taken over
        self.assertEqual(AS.lock_holder()["owner"], "new run")
        AS.release_lock()

    def test_lock(self):
        self.assertTrue(AS.acquire_lock("a"))
        self.assertFalse(AS.acquire_lock("b"))
        self.assertEqual(AS.lock_holder()["owner"], "a")
        AS.release_lock()
        self.assertTrue(AS.acquire_lock("b"))
        AS.release_lock()

    def test_interrupted_runs_marked(self):
        rid = AS.start_run_record(D, "manual")
        AS.mark_interrupted_runs()
        self.assertEqual(next(r for r in AS.list_runs() if r["id"] == rid)["status"], "interrupted")

    def test_validate_download_against_profile(self):
        d = tempfile.mkdtemp()
        f = raw_export(os.path.join(d, "van.xlsx"), "Outlet Wise SKU Sales Report",
                       [("Report Type", "Van Selling"), ("From Date", D), ("To Date", D)], SKU_H,
                       [sku("Alpha", "Van Selling", "INV/1", "Thing", 1, 100)])
        prof = {"feeds_into": "sku_van", "filters": [
            {"label": "Report Type", "type": "dropdown", "value": "Van Selling"},
            {"label": "Date From", "type": "date", "date_mode": "report_date"},
            {"label": "Date To", "type": "date", "date_mode": "report_date"}]}
        v = E.validate_download(f, prof, D)
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["rows"], 1)
        v = E.validate_download(f, prof, "2026-10-27")                     # wrong day
        self.assertFalse(v["ok"])
        self.assertIn("Date From", v["message"])
        v = E.validate_download(f, {**prof, "feeds_into": "sku_pre"}, D)   # wrong report kind
        self.assertFalse(v["ok"])
        prof2 = {**prof, "filters": [{"label": "Report Type", "type": "dropdown", "value": "Pre Sales Order - Pending Only"}]}
        self.assertFalse(E.validate_download(f, prof2, D)["ok"])            # wrong filter value

    def test_empty_dataset_clears_old_rows(self):
        conn = db.get_connection()
        conn.execute("INSERT OR REPLACE INTO sfa_bills (bill_no, dataset, bill_date, kind, territory, amount) "
                     "VALUES ('SO/X', 'sales_orders', ?, 'SO', 'Alpha', 10)", (D,))
        conn.commit()
        conn.close()
        sfa_store.record_empty_dataset("sales_orders", D)
        conn = db.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sfa_bills WHERE bill_no='SO/X'").fetchone()[0], 0)
        conn.close()
        st = {x["dataset"]: x for x in sfa_store.get_dataset_status(D)["datasets"]}
        self.assertTrue(st["sales_orders"]["loaded"])
        self.assertEqual(st["sales_orders"]["rows"], 0)

    def test_data_saver_setting_and_usage(self):
        ok, _ = AS.save_automation_settings({"data_saver": "1"})
        self.assertTrue(ok)
        self.assertEqual(AS.get_automation_settings()["data_saver"], "1")
        AS.save_automation_settings({"data_saver": "0"})
        rid = AS.start_run_record(D, "manual", None)
        AS.finish_run_record(rid, "success", [], None, "ok", data_bytes=2_000_000, blocked=7, data_saver=True)
        run = next(r for r in AS.list_runs() if r["id"] == rid)
        self.assertEqual((run["data_bytes"], run["blocked"], run["data_saver"]), (2_000_000, 7, 1))
        u = AS.data_usage_summary()
        self.assertEqual(u["last_bytes"], 2_000_000)
        self.assertGreaterEqual(u["today_bytes"], 2_000_000)
        self.assertEqual(u["avg_with_saver"], 2_000_000)

    def test_meter_counts_http_downloads_only(self):
        m = E.NetMeter(data_saver=True)
        f = os.path.join(_TMP_HOME, "dl.bin")
        open(f, "wb").write(b"x" * 1234)
        m.add_download("blob:https://sfa/abc", f)
        self.assertEqual(m.bytes, 0)
        m.add_download("https://sfa/export.xlsx", f)
        self.assertEqual(m.bytes, 1234)


if __name__ == "__main__":
    unittest.main()
