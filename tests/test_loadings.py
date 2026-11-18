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
    filters = [{"label": "Territory", "type": "dropdown", "value": f"{name} TERRITORY"},
               {"label": "Distributor", "type": "dropdown", "value": value if value is not None else name},
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
        self.assertTrue(e["problems"])             # no territory known -> flagged

    def test_view_loadings_filters_put_territory_first_and_drop_distributor(self):
        d = dist("Dist T")
        # Territory, then both dates set to the loading's day (SFA: "Please select from and to dates!"); no Distributor
        self.assertEqual([(f["label"], f["value"]) for f in LS.view_filters(d, "2026-11-18")],
                         [("Territory", "Dist T TERRITORY"), ("Date From", "2026-11-18"), ("Date To", "2026-11-18")])
        import datetime as _d
        self.assertEqual(LS.view_filters(d)[1]["value"], _d.date.today().isoformat())
        self.assertEqual(LS.loading_day("Nov 17, 2026"), "2026-11-17")
        self.assertEqual(LS.loading_day("11/17/2026"), "2026-11-17")
        self.assertIsNone(LS.loading_day(""))
        # no Territory filter: the territory that worked on Add Loading, then SFA's users list
        e = {"filters": [{"label": "Distributor", "value": "X"}], "add_fields": {"territory": "Lakmee TERRITORY"}, "territory": "Old"}
        self.assertEqual(LS.territory_of(e), "Lakmee TERRITORY")
        self.assertEqual(LS.territory_of({"filters": [], "add_fields": {}, "territory": "From Users"}), "From Users")
        self.assertEqual(LS.view_filters({"filters": [], "add_fields": {}, "territory": ""}), [])
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
        self.assertEqual(c1["filters"][1]["value"], "A")

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
        # the same product code at different prices is separate lines: quantities go by code@price
        dup = LC._products({"rows": [
            {"name": "Buddy 50g", "code": "PRO_0007", "price": "15.00", "stock": "60", "last_unloaded": "0"},
            {"name": "Buddy 50g", "code": "PRO_0007", "price": "16.00", "stock": "60", "last_unloaded": "0"}]})
        self.assertEqual([p["key"] for p in dup], ["PRO_0007@15.0000", "PRO_0007@16.0000"])
        self.assertEqual(LC.check_quantities(dup, {"PRO_0007@16.0000": 60}), [])
        self.assertTrue(any("only 60" in x for x in LC.check_quantities(dup, {"PRO_0007@15.0000": 61})))
        self.assertEqual(LC._norm_qty({"PRO_0007": 5}, dup), {"PRO_0007": 5.0})   # ambiguous old key isn't guessed
        self.assertTrue(AS.get_automation_settings()["add_loading_dry_run"] == "1")           # safe by default
        # SFA's own pre-filled quantity comes through as its suggestion (never negative, empty = 0)
        sug = LC._products({"rows": [
            {"name": "A", "code": "P1", "price": "10", "stock": "50", "last_unloaded": "0", "qty": "24"},
            {"name": "B", "code": "P2", "price": "10", "stock": "50", "last_unloaded": "0", "qty": ""},
            {"name": "C", "code": "P3", "price": "10", "stock": "50", "last_unloaded": "0", "qty": "-3"}]})
        self.assertEqual([p["suggested"] for p in sug], [24.0, 0.0, 0.0])

    def test_sfa_confirmation_alert_is_found_and_answered_yes(self):
        """SFA's own 'Confirmation Alert ! Do you want to submit this record' [Yes] [No] box (made-up copy)."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("Playwright isn't installed")
        import asyncio
        import loading_create as LC
        page_html = """<nav><a class="btn" href="#">Close</a> menu text here for the page</nav>
          <button id="save">SAVE</button><table><tr><td>Mega Dadayam Batta 110g</td></tr></table>"""
        alert = """<div style="position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:1000">
          <div style="width:320px;margin:120px auto;background:#fff;padding:20px;text-align:center">
          <div style="font-size:18px">Confirmation Alert !</div><div>Do you want to submit this record</div>
          <BTN id="yes" style="background:#22c55e" onclick="window.answer='yes';this.closest('div[style*=fixed]').remove()">Yes</BTN>
          <BTN id="no" style="background:#000;color:#fff" onclick="window.answer='no'">No</BTN></div></div>"""

        async def go(tag):
            async with async_playwright() as p:
                try:
                    b = await p.chromium.launch()
                except Exception as e:
                    self.skipTest(f"No browser for Playwright here: {e}")
                page = await b.new_page()
                await page.set_content(page_html)
                self.assertIsNone(await LC._dialog(page))                 # the page's own Close link isn't a pop-up
                await page.set_content(page_html + alert.replace("BTN", tag))
                dlg = await LC._dialog(page)
                clicked = await LC._dismiss(page, ("yes", "ok", "confirm"))
                answer = await page.evaluate("window.answer || ''")
                gone = await LC._dialog(page)
                await b.close()
                return dlg, clicked, answer, gone

        for tag in ("button", "a class='btn'".split()[0]):
            dlg, clicked, answer, gone = asyncio.run(go(tag))
            self.assertEqual(dlg["title"], "Confirmation Alert !")
            self.assertIn("Do you want to submit this record", dlg["all"])
            self.assertFalse(LC._is_error(dlg))
            # SFA's refusal when the rep hasn't confirmed the previous loading in the mobile app
            self.assertTrue(LC._is_error({"all": "Previously loaded quantities are not confirmed by the sales rep"}))
            self.assertEqual((clicked, answer, gone), ("yes", "yes", None))

    def test_view_loadings_pending_row_is_found(self):
        """Made-up copies of SFA's View Loadings table: DIS CONFIRMATION STATUS 'Pending' + a ✓ under CONFIRM."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("Playwright isn't installed")
        import asyncio
        head = ("<tr><th>LOADING NUMBER</th><th>REP NAME</th><th>CREATED BY</th><th>DIS CONFIRMATION STATUS</th><th>CONFIRM</th>"
                "<th>REP CONFIRMATION STATUS</th><th>LOADING DATE</th><th>LOADING TIME</th><th>EXPORT</th><th>VIEW</th></tr>")
        rows = ("<tr><td>LS/1/2/3/00351</td><td>Test Rep</td><td>Someone</td><td>Pending</td><td><a href='#'><i class='fa fa-check'></i></a></td>"
                "<td>Pending</td><td>Nov 17, 2026</td><td>20:24:55</td><td><i></i></td><td><i></i></td></tr>"
                "<tr><td>LS/1/2/3/00130</td><td>Other Rep</td><td>Someone</td><td>Confirmed on 2026-11-17 18:11:00</td><td>Confirmed</td>"
                "<td>Confirmed on 2026-11-17 18:14:21</td><td>Nov 17, 2026</td><td>18:10:11</td><td><i></i></td><td><i></i></td></tr>")
        plain = f"<table><thead>{head}</thead><tbody>{rows}</tbody></table>"
        split = (f"<div class='dataTables_wrapper'><div class='dataTables_scroll'><div class='dataTables_scrollHead'><table><thead>{head}"
                 f"</thead></table></div><div class='dataTables_scrollBody'><table><thead style='visibility:collapse'>{head}</thead>"
                 f"<tbody>{rows}</tbody></table></div></div></div>")
        empty = f"<table><thead>{head}</thead><tbody><tr><td colspan='10'>No data available in table</td></tr></tbody></table>"

        async def go():
            async with async_playwright() as p:
                try:
                    b = await p.chromium.launch()
                except Exception as e:
                    self.skipTest(f"No browser for Playwright here: {e}")
                page = await b.new_page()
                out = []
                for html in (plain, split, empty):
                    await page.set_content(html)
                    out.append(await page.evaluate(LA._RESULTS_JS, ["t"]))
                await b.close()
                return out

        a, b2, c = asyncio.run(go())
        for r in (a, b2):
            self.assertTrue(r["table"])
            self.assertEqual((r["pending"], r["pending_count"]), (0, 1))
            self.assertEqual(r["rows"][0]["number"], "LS/1/2/3/00351")
            self.assertEqual(r["rows"][0]["status"], "Pending")
            self.assertFalse(r["rows"][1]["pending"])
        self.assertEqual((c["table"], c["rows"], c["pending"]), (True, [], -1))

    def test_sfa_error_after_search_is_reported(self):
        """SFA's 'Error! Please select from and to dates!' pop-up is read, not waited on for a minute."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("Playwright isn't installed")
        import asyncio
        html = """<div class="swal2-container" style="position:fixed;inset:0"><div class="swal2-popup" role="dialog">
          <h2 class="swal2-title">Error!</h2><div class="swal2-html-container">Please select from and to dates!</div>
          <button class="swal2-confirm">OK</button></div></div>"""

        async def go():
            async with async_playwright() as p:
                try:
                    b = await p.chromium.launch()
                except Exception as e:
                    self.skipTest(f"No browser for Playwright here: {e}")
                page = await b.new_page()
                await page.set_content(html)
                said = await page.evaluate(LA._SFA_ERROR_JS)
                await page.set_content("<button>SEARCH</button><table></table>")
                none = await page.evaluate(LA._SFA_ERROR_JS)
                await b.close()
                return said, none

        said, none = asyncio.run(go())
        self.assertEqual(said, "Error! Please select from and to dates!")
        self.assertEqual(none, "")

    def test_confirm_page_is_read_and_confirmed_with_yes(self):
        """Made-up copy of SFA's CONFIRM LOADING page: read it, press the green Confirm, answer 'Yes, confirm it!'."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("Playwright isn't installed")
        import asyncio
        cell = lambda v: f"<td><input value='{v}'></td>"
        rows = "".join(f"<tr>{cell(n)}{cell(c)}{cell(p)}{cell(st)}{cell(q)}<td>0.0000</td>{cell(a)}</tr>" for n, c, p, st, q, a in [
            ("LakmeeSoy Chilli Chicken 60g", "PRO_138", "87.000", "29767", "500", "43500.00"),
            ("Black Chicken 85g with C&G", "PRO_0016", "140.000", "7", "2", "280.00")])
        page_html = f"""<h3>CONFIRM LOADING</h3>
          <div><label>Date</label><input value="11/17/2026"></div><div><label>Time</label><input value="20:24:55"></div>
          <div><label>Loading Number</label><input value="LS/1/2/3/00351"></div><div><label>Sales Rep</label><input value="Test Rep"></div>
          <table><thead><tr><th>PRODUCT</th><th>PRODUCT CODE</th><th>PRICE</th><th>AVAIALBLE STOCK</th><th>ACCEPTED QUANTITY</th>
          <th>VOLUME (MT)</th><th>AMOUNT</th></tr></thead><tbody>{rows}
          <tr><td colspan="4"><b>Total</b></td><td>502.00</td><td>0.000</td><td>43,780.00</td></tr></tbody></table>
          <button class="btn btn-success" onclick="document.body.insertAdjacentHTML('beforeend', popup)">Confirm</button>
          <script>var popup = `<div class="swal2-container" style="position:fixed;inset:0"><div class="swal2-popup" role="dialog">
            <h2 class="swal2-title">Are you sure!</h2><div class="swal2-html-container">Are you sure to confirm?</div>
            <button class="swal2-confirm" onclick="window.done='yes';this.closest('.swal2-container').remove()">Yes, confirm it!</button>
            <button class="swal2-cancel" onclick="window.done='cancel'">Cancel</button></div></div>`;</script>"""

        async def go():
            async with async_playwright() as p:
                try:
                    b = await p.chromium.launch()
                except Exception as e:
                    self.skipTest(f"No browser for Playwright here: {e}")
                page = await b.new_page()
                await page.set_content(page_html)
                data = await page.evaluate(LA._READ_CONFIRM_JS)
                await LA._press_confirm(page, 0, 0, "Made-up Distributor")
                done = await page.evaluate("window.done || ''")
                await b.close()
                return data, done

        data, done = asyncio.run(go())
        self.assertEqual(data["header"]["loading_number"], "LS/1/2/3/00351")
        self.assertEqual([(x["code"], x["qty"], x["stock"]) for x in data["products"]],
                         [("PRO_138", "500", "29767"), ("PRO_0016", "2", "7")])
        self.assertEqual(data["totals"]["amount"], "43,780.00")
        self.assertEqual(done, "yes")

    def test_unconfirmed_add_is_not_reported_as_saved(self):
        """SAVE pressed but SFA gave no success message and View Loadings doesn't list it: a problem, with its log kept."""
        import activity
        d = {"name": "Made-up Distributor"}
        rid = LS.record_add(d, {"saved": False, "number": "LS/1/2/3/00001", "sfa_message": "No message from SFA after SAVE."},
                            False, "13:23 Pressed SAVE.\n13:24 Not on View Loadings.")
        run = next(r for r in LS.list_runs(50) if r["id"] == rid)
        self.assertEqual(run["status"], "unconfirmed")
        self.assertIn("NOT confirmed", run["message"])
        row = next(r for r in activity.loadings() if r["run_id"] == rid)
        self.assertEqual((row["state"], row["status"]), ("add_unconfirmed", "fail"))
        ok_id = LS.record_add(d, {"saved": True, "verified": True, "number": "LS/1/2/3/00002"}, False, "log")
        self.assertEqual(next(r for r in LS.list_runs(50) if r["id"] == ok_id)["status"], "done")

    def test_add_loading_clears_sfa_prefilled_lines_you_left_at_zero(self):
        """On a made-up copy of SFA's product table with pre-filled quantities, only the chosen lines keep a number."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("Playwright isn't installed")
        import asyncio
        import loading_create as LC
        html = """<table><thead><tr><th>PRODUCT</th><th>PRODUCT CODE</th><th>PRICE</th><th>AVAILABLE STOCK</th>
          <th>LAST UNLOADED QTY</th><th>QUANTITY</th><th>AMOUNT</th></tr></thead><tbody>
          <tr><td>Soy Chicken 60g</td><td>PRO_137</td><td>87.00</td><td>10</td><td>0</td><td><input value="6"></td><td></td></tr>
          <tr><td>Soy Curry 60g</td><td>PRO_140</td><td>87.00</td><td>25</td><td>4</td><td><input value="12"></td><td></td></tr>
          <tr><td>Buddy 50g</td><td>PRO_0007</td><td>43.00</td><td>60</td><td>60</td><td><input value="0"></td><td></td></tr>
          </tbody></table>"""

        async def go():
            async with async_playwright() as p:
                try:
                    b = await p.chromium.launch()
                except Exception as e:
                    self.skipTest(f"No browser for Playwright here: {e}")
                page = await b.new_page()
                await page.set_content(html)
                tbl = await page.evaluate(LC._TABLE_JS, ["t1"])
                self.assertEqual([p_["suggested"] for p_ in LC._products(tbl)], [6.0, 12.0, 0.0])
                logs = []
                want = LC._norm_qty({"PRO_140": 20, "PRO_0007": 5}, LC._products(tbl))   # Soy Chicken left at 0
                cleared = await LC._apply_quantities(page, tbl, want, logs.append)
                after = await page.evaluate(LC._TABLE_JS, ["t2"])
                await b.close()
                return cleared, [r["qty"] for r in after["rows"]]

        cleared, vals = asyncio.run(go())
        self.assertEqual(cleared, 1)
        self.assertEqual(vals, ["0", "20", "5"])

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
