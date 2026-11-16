"""
Tests for the Overview boxes (made-up data): territories logged in today of the
working ones, active distributors, today's invoices, and the last SFA sync.

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
import outlet_store  # noqa: E402
import rep_attendance as R  # noqa: E402
import sfa_store  # noqa: E402
import loading_store  # noqa: E402
import overview_kpis as K  # noqa: E402
import fake_attendance as F  # noqa: E402

NOW = datetime(2026, 11, 13, 13, 50)
TODAY = "2026-11-13"


class OverviewKpisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        outlet_store.init_schema()
        R.init_schema()
        sfa_store.init_schema()
        loading_store.init_schema()

    def setUp(self):     # the test modules share one database: start from empty tables
        conn = db.get_connection()
        for t in ("att_rows", "att_days", "att_runs", "ou_territories", "sfa_bills", "sfa_datasets", "distributors",
                  "loading_runs"):
            conn.execute(f"DELETE FROM {t}")
        for t in ("seq_runs",):
            try:
                conn.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        conn.commit()
        conn.close()
        AS.save_automation_settings({"att_working": "manual"})

    def test_empty_install_says_so(self):
        k = K.overview(NOW)
        self.assertIsNone(k["logged_in"]["value"])
        self.assertIsNone(k["invoices"]["value"])
        self.assertIsNone(k["distributors"]["value"])
        self.assertIsNone(k["last_sync"]["value"])

    def test_filled_from_sfa_data(self):
        for t in ("Alpha Town", "Beta Bay", "Gamma Hills", "Delta Port", "Zeta Point"):
            outlet_store.add_territory(t)
        R.save_day(R.read_excel(F.write_export(os.path.join(_TMP_HOME, "a.xlsx"), TODAY)), "sfa")
        conn = db.get_connection()
        for i, (kind, terr, amt) in enumerate([("INV", "Alpha Town", 1000), ("INV", "Beta Bay", 2500.5),
                                               ("SO", "Gamma Hills", 700), ("INV", "Alpha Town", 300)]):
            conn.execute("INSERT INTO sfa_bills (bill_no, dataset, bill_date, kind, territory, amount) VALUES (?,?,?,?,?,?)",
                         (f"B{i}", "invoices" if kind == "INV" else "sales_orders", TODAY, kind, terr, amt))
        conn.execute("INSERT INTO sfa_bills (bill_no, dataset, bill_date, kind, territory, amount) VALUES ('OLD','invoices','2026-11-12','INV','X',5)")
        conn.execute("INSERT INTO sfa_datasets (dataset, data_date, rows, imported_at) VALUES ('invoices', ?, 3, ?)", (TODAY, TODAY + "T13:30:00"))
        conn.commit()
        conn.close()
        loading_store.save_distributor({"name": "Test Distributor A", "frequency": "daily"})
        loading_store.save_distributor({"name": "Test Distributor B"})
        k = K.overview(NOW)
        self.assertEqual((k["logged_in"]["value"], k["logged_in"]["of"]), (4, 5))
        self.assertTrue(k["logged_in"]["is_today"])
        self.assertIn("1 not logged in", k["logged_in"]["foot"])
        self.assertEqual(k["invoices"]["value"], 3)
        self.assertIn("Rs 3,800", k["invoices"]["foot"])
        self.assertIn("1 SOs", k["invoices"]["foot"])
        self.assertEqual(k["distributors"]["value"], 2)
        self.assertIn("1 with daily loadings", k["distributors"]["foot"])
        self.assertIsNotNone(k["last_sync"]["value"])
        self.assertIn(k["last_sync"]["what"], ("SFA reports", "Rep attendance"))
        # the next morning, before anything is read: yesterday's numbers, marked
        k2 = K.overview(datetime(2026, 11, 14, 7, 0))
        self.assertFalse(k2["invoices"]["is_today"])
        self.assertIn("latest: 13 Nov", k2["invoices"]["foot"])
        self.assertFalse(k2["logged_in"]["is_today"])


if __name__ == "__main__":
    unittest.main()
