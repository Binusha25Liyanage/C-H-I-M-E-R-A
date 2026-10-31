"""
Tests for Distributor Loadings: distributor setup (Daily / On request,
filters, copy), the approval queue (one pending loading per distributor,
skip), and the "changed since you approved" check. No browser needed.

Run:  py -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
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
import loading_store as LS  # noqa: E402
import loading_automation as LA  # noqa: E402


def dist(name, freq="daily", value=None):
    filters = [{"label": "Distributor", "type": "dropdown", "value": value if value is not None else name},
               {"label": "Date From", "type": "date", "date_mode": "today", "value": ""}]
    ok, msg, d = LS.save_distributor({"name": name, "frequency": freq, "filters": filters})
    assert ok, msg
    return d


LOADING = {"loading_number": "LD-001", "rep": "Rep A",
           "products": [{"product": "Tea 100g", "code": "P1", "qty": "10"}, {"product": "Tea 200g", "code": "P2", "qty": "0"}],
           "totals": {"qty": "10", "amount": "1,500.00"}}


class LoadingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        LS.init_schema()

    def setUp(self):
        conn = db.get_connection()
        conn.execute("DELETE FROM distributors")
        conn.execute("DELETE FROM loading_items")
        conn.execute("DELETE FROM loading_runs")
        conn.commit()
        conn.close()

    def test_save_and_problems(self):
        d = dist("Dist A")
        self.assertEqual(d["problems"], [])
        ok, msg, _ = LS.save_distributor({"name": "dist a", "frequency": "daily", "filters": []})
        self.assertFalse(ok)                       # duplicate name (any case)
        ok, msg, e = LS.save_distributor({"name": "No filters", "frequency": "daily", "filters": []})
        self.assertTrue(ok)
        self.assertTrue(e["problems"])             # no Distributor filter -> flagged
        ok, msg, _ = LS.save_distributor({"name": "Bad", "frequency": "weekly", "filters": []})
        self.assertFalse(ok)

    def test_daily_ids_skip_request_and_broken(self):
        a = dist("A", "daily")
        dist("B", "request")
        LS.save_distributor({"name": "C", "frequency": "daily", "filters": []})
        self.assertEqual(LS.daily_ids(), [a["id"]])

    def test_set_frequency_bulk(self):
        a, b = dist("A", "request"), dist("B", "request")
        self.assertTrue(LS.set_frequency([a["id"], b["id"]], "daily"))
        self.assertEqual(sorted(LS.daily_ids()), sorted([a["id"], b["id"]]))
        self.assertFalse(LS.set_frequency([a["id"]], "sometimes"))

    def test_duplicate_gets_new_name(self):
        a = dist("A")
        ok, _, c1 = LS.duplicate_distributor(a["id"])
        ok2, _, c2 = LS.duplicate_distributor(a["id"])
        self.assertTrue(ok and ok2)
        self.assertEqual((c1["name"], c2["name"]), ("A (copy)", "A (copy 2)"))
        self.assertEqual(c1["filters"][0]["value"], "A")

    def test_one_pending_per_distributor(self):
        a = dist("A")
        r1 = LS.start_run("prepare", "manual")
        old = LS.add_item(r1, a, "pending_approval", **LOADING)
        r2 = LS.start_run("prepare", "schedule")
        new = LS.add_item(r2, a, "pending_approval", **LOADING)
        self.assertEqual(LS.get_item(old)["state"], "superseded")
        self.assertEqual([i["id"] for i in LS.pending_items()], [new])
        self.assertEqual(LS.list_distributors()[0]["last"]["state"], "pending_approval")

    def test_skip_only_touches_pending(self):
        a, b = dist("A"), dist("B")
        r = LS.start_run("prepare", "manual")
        p = LS.add_item(r, a, "pending_approval", **LOADING)
        n = LS.add_item(r, b, "nothing_pending")
        LS.skip_items([p, n])
        self.assertEqual(LS.get_item(p)["state"], "skipped")
        self.assertEqual(LS.get_item(n)["state"], "nothing_pending")
        self.assertEqual(LS.pending_items(), [])
        self.assertEqual(LS.today_summary(), {"skipped": 1, "nothing_pending": 1})

    def test_interrupted_runs_are_closed(self):
        r = LS.start_run("confirm", "manual")
        LS.mark_interrupted()
        self.assertNotEqual(LS.list_runs()[0]["status"], "running")

    def test_changed_loading_is_detected(self):
        self.assertEqual(LA._differences(LOADING, LOADING), [])
        changed = {**LOADING, "products": [{"product": "Tea 100g", "code": "P1", "qty": "10"},
                                           {"product": "Tea 200g", "code": "P2", "qty": "3"}],
                   "totals": {"qty": "13", "amount": "1,950.00"}}
        diff = LA._differences(LOADING, changed)
        self.assertTrue(any("P2" in d for d in diff))
        self.assertTrue(any("total amount" in d for d in diff))
        other = {**LOADING, "loading_number": "LD-002"}
        self.assertTrue(any("loading number" in d for d in LA._differences(LOADING, other)))


if __name__ == "__main__":
    unittest.main()
