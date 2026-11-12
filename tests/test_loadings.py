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
        self.assertEqual(LS.set_frequency([a["id"], b["id"]], "daily"), (True, []))
        self.assertEqual(sorted(LS.daily_ids()), sorted([a["id"], b["id"]]))
        self.assertFalse(LS.set_frequency([a["id"]], "sometimes")[0])

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


class DistributorSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        LS.init_schema()

    def setUp(self):
        conn = db.get_connection()
        conn.execute("DELETE FROM distributors")
        conn.commit()
        conn.close()

    @staticmethod
    def export(rows):
        """A file shaped like SFA's User Profile export (title rows, filter rows, then the table)."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Lakmee Exports Lanka Company (Pvt) Ltd"])
        ws.append(["User Profile"])
        ws.append(["User Type :", "DISTRIBUTOR"])
        ws.append(["USER CODE", "NAME", "USER NAME", "TELEPHONE", "ADDRESS", "DISTRICT", "CITY", "USER TYPE", "RSM", "ASM",
                   "ASE", "DISTRIBUTOR", "REGION", "AREA", "SA", "TERRITORY"])
        for r in rows:
            ws.append(r)
        path = os.path.join(tempfile.mkdtemp(), "User_Profile.xlsx")
        wb.save(path)
        return path

    def row(self, code, name, terr="Galnewa", asm="ASM A", phone=771234567):
        return [code, name, code, phone, "-", "-", "-", "DISTRIBUTOR", "RSM", asm, "ASE", "-", "R", "Area", "SA", terr]

    def test_read_and_apply(self):
        import distributor_sync as DS
        p = self.export([self.row("DB001", "DB Vacant 1-Hatton", "Hatton"), self.row("DB002", "N Nirothkanth"),
                         self.row("DB152", "W M A T Wanninayaka", "-"), self.row("DB152", "W M A T Wanninayaka", "Galgamuwa"),
                         ["SR001", "A Rep", "SR001", "-", "-", "-", "-", "SALES REP"] + ["-"] * 8])
        rows = DS.read_users_excel(p, status="active")
        self.assertEqual([r["code"] for r in rows], ["DB001", "DB002", "DB152", "DB152"])   # the rep is ignored
        self.assertEqual(rows[1]["phone"], "771234567")
        LS.save_distributor({"name": "N Nirothkanth", "frequency": "daily",
                             "filters": [{"label": "Distributor", "type": "dropdown", "value": "N Nirothkanth"}]})
        inactive = DS.read_users_excel(self.export([self.row("DB002", "N Nirothkanth")]), status="inactive")
        active = [r for r in rows if r["code"] != "DB002"]
        res = LS.apply_sfa_list(active + inactive)
        self.assertEqual(res["total"], 3)
        ds = {d["code"]: d for d in LS.list_distributors()}
        self.assertEqual(len(ds), 3)                                                # hand-made one linked, not doubled
        self.assertTrue(ds["DB001"]["vacant"])
        self.assertEqual(ds["DB152"]["territory"], "Galgamuwa")                     # duplicate rows merged
        self.assertEqual(ds["DB001"]["filters"][0]["value"], "DB Vacant 1-Hatton")  # ready to prepare
        self.assertEqual(ds["DB001"]["frequency"], "request")
        self.assertTrue(ds["DB002"]["blocked"])
        self.assertEqual(ds["DB002"]["frequency"], "request")                       # was Daily; inactive can't be
        self.assertNotIn(ds["DB002"]["id"], LS.daily_ids())
        self.assertEqual(LS.set_frequency([ds["DB002"]["id"], ds["DB001"]["id"]], "daily"), (True, ["N Nirothkanth"]))
        self.assertEqual(LS.daily_ids(), [ds["DB001"]["id"]])

    def test_add_loading_fields_and_quantities(self):
        import loading_create as LC
        LS.apply_sfa_list([{"code": "DB020", "name": "V Pathmasiri", "territory": "Colombo SMMT", "status": "active"}])
        d = next(x for x in LS.list_distributors() if x["code"] == "DB020")
        self.assertEqual(LC.fields_for(d), {"territory": "Colombo SMMT", "distributor": "V Pathmasiri", "sales_rep": ""})
        LS.set_add_fields(d["id"], {"territory": "Colombo SMMT", "distributor": "V Pathmasiri", "sales_rep": "CSR Colombo SMMT - A",
                                    "junk": "x"})
        d = LS.get_distributor(d["id"])
        self.assertEqual(LC.fields_for(d)["sales_rep"], "CSR Colombo SMMT - A")              # remembered for next time
        prods = [{"name": "Buddy Chicken 50g", "code": "PRO_0007", "stock": 90.0}, {"name": "Lakposha 200g", "code": "PRO_163", "stock": 5.0}]
        self.assertEqual(LC.check_quantities(prods, {"PRO_0007": 90, "PRO_163": 0}), [])
        self.assertTrue(any("only 5" in x for x in LC.check_quantities(prods, {"PRO_163": 6})))  # stock fell since reading
        self.assertTrue(LC.check_quantities(prods, {"PRO_0007": 0}))                          # nothing to load
        self.assertTrue(any("isn't on the Add Loading page" in x for x in LC.check_quantities(prods, {"PRO_999": 2})))
        self.assertTrue(any("negative" in x for x in LC.check_quantities(prods, {"PRO_0007": -1, "PRO_163": 1})))
        self.assertTrue(AS.get_automation_settings()["add_loading_dry_run"] == "1")           # safe by default

    def test_rename_and_gone(self):
        LS.apply_sfa_list([{"code": "DB010", "name": "Old Name", "status": "active"},
                           {"code": "DB011", "name": "Other", "status": "active"}])
        res = LS.apply_sfa_list([{"code": "DB010", "name": "New Name", "status": "active"}])
        ds = {d["code"]: d for d in LS.list_distributors()}
        self.assertEqual(res["renamed"], ["Old Name → New Name"])
        self.assertEqual(ds["DB010"]["filters"][0]["value"], "New Name")        # filter follows SFA's name
        self.assertEqual(res["gone"], ["Other"])
        self.assertTrue(ds["DB011"]["blocked"])                                  # kept, but can't be used

    def test_wrong_file(self):
        import distributor_sync as DS
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["Invoice No", "Amount"])
        p = os.path.join(tempfile.mkdtemp(), "x.xlsx")
        wb.save(p)
        with self.assertRaises(DS.SyncError):
            DS.read_users_excel(p)


if __name__ == "__main__":
    unittest.main()
