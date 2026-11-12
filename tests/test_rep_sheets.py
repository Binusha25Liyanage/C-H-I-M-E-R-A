"""
Tests for Outlet Updates › Rep sheets: reading the reps' route sheets (the
format in use today and Chimera's own), matching them to SFA's route list,
the preview rules (rep's order, blanks after in SFA's order, Top 15 ->
Mandatory, others unchanged, your own choices kept), approval, and the
Activity Monitor record. No browser needed; all data here is made up.

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
import sequence_store as SS  # noqa: E402
import sequence_reader as SR  # noqa: E402
import sequence_automation as SQ  # noqa: E402
import openpyxl  # noqa: E402

ROUTES = [{"code": "ROUI0101", "name": "Town upto Lake", "territory": "Testland", "region": "R", "area": "A", "status": "ACTIVE"},
          {"code": "ROUI0102", "name": "Lake upto Hill", "territory": "Testland", "region": "R", "area": "A", "status": "ACTIVE"},
          {"code": "TR0009", "name": "Other Route", "territory": "Elsewhere", "region": "R", "area": "B", "status": "ACTIVE"}]


def rep_sheet(rows, code="ROUI0101", route="Town upto Lake", territory="Testland", name=None, footer=True):
    """A file shaped like the route sheets reps fill today."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = route[:31]
    ws.append([f"Active Outlet List - {route}"])
    ws.append([f"Territory: {territory}     Route: {route}     Route Code: {code}     Distributor: D Dist"])
    ws.append(["Sales Rep: CSR Testland - Rep     Status: Active     Outlets: 9     Printed: 11 Nov 2026, 15:07"])
    ws.append([])
    ws.append([])
    ws.append(["No", "Outlet Code", "Outlet Name", "New Sequence", "Top 15", "Remark", "Demand Product"])
    for i, r in enumerate(rows, 1):
        ws.append([i] + list(r))
    if footer:
        ws.append([])
        ws.append(["Prepared by: ______", None, "Checked by: ______", None, "Date: ______"])
    path = os.path.join(tempfile.mkdtemp(), name or f"01_{route.replace(' ', '_')}_{code}.xlsx")
    wb.save(path)
    return path


def sfa(code, seq, state="Optional"):
    return {"code": code, "name": code.split("/")[-1], "edit_id": f"http://sfa/it/level-eight/edit/{seq}", "seq": str(seq), "state": state}


class RepSheetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        SS.init_schema()

    def setUp(self):
        conn = db.get_connection()
        for t in ("sfa_routes", "seq_sheets", "seq_lines", "seq_runs"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()
        SS.apply_routes(ROUTES)

    # ---------------------------------------------------------------- reading
    def test_top_marks_and_phones(self):
        for v in (1, 15, "✓", "Y", "yes", "x", "Top", True):
            self.assertTrue(SR.top_mark(v)[0], v)
        for v in (None, "", 0, "0", "no", "N", "-", False):
            self.assertFalse(SR.top_mark(v)[0], v)
        self.assertEqual(SR.top_mark(3), (True, 3))
        self.assertEqual([SQ.normalize_phone(x) for x in ("0761282723", "761282723", "+94761282723", "076 128-2723", "", "12345")],
                         ["0761282723", "0761282723", "0761282723", "0761282723", "0000000000", "0000000000"])

    def test_read_rep_format(self):
        p = rep_sheet([("OUT/1/001", "A Stores", 2, "✓", "", ""), ("OUT/1/002", "B Stores", 1, "", "Closed Sundays", ""),
                       ("OUT/1/003", "C Stores", "", 3, "", "Noodles 400g"), ("OUT/1/004", "D Stores", "two", "", "", "")])
        [s] = SR.read_workbook(p)
        self.assertEqual((s["format"], s["route_code"], s["territory"], s["rep"], s["distributor"]),
                         ("rep", "ROUI0101", "Testland", "CSR Testland - Rep", "D Dist"))
        self.assertEqual([l["code"] for l in s["lines"]], ["OUT/1/001", "OUT/1/002", "OUT/1/003", "OUT/1/004"])  # footer ignored
        self.assertEqual([(l["rep_seq"], l["top"], l["top_rank"]) for l in s["lines"]],
                         [("2", True, None), ("1", False, None), ("", True, 3), ("", False, None)])
        self.assertEqual((s["lines"][1]["remark"], s["lines"][2]["demand"]), ("Closed Sundays", "Noodles 400g"))
        self.assertTrue(any("isn't a whole number" in x["text"] for x in s["problems"]))
        self.assertFalse(any(x["level"] == "error" for x in s["problems"]))

    def test_route_checks(self):
        blank = rep_sheet([("OUT/1/001", "A", "", "", "", "")])
        self.assertTrue(any("Nothing is filled in" in x["text"] for x in SR.read_workbook(blank)[0]["problems"]))
        wrong = rep_sheet([("OUT/1/001", "A", 1, "", "", "")], code="ROUI9999", route="Unknown Road")
        self.assertTrue(any("isn't in SFA's route list" in x["text"] for x in SR.read_workbook(wrong)[0]["problems"]))
        other = rep_sheet([("OUT/1/001", "A", 1, "", "", "")], territory="Elsewhere")
        self.assertTrue(any("belongs to" in x["text"] for x in SR.read_workbook(other)[0]["problems"]))
        renamed = rep_sheet([("OUT/1/001", "A", 1, "", "", "")], route="Town to Lake (old name)")
        s = SR.read_workbook(renamed)[0]
        self.assertEqual(s["route_name"], "Town upto Lake")                 # SFA's name, matched by code
        dups = rep_sheet([("OUT/1/001", "A", 1, "", "", ""), ("OUT/1/002", "B", 1, "", "", ""), ("OUT/1/001", "A", 3, "", "", "")])
        texts = " ".join(x["text"] for x in SR.read_workbook(dups)[0]["problems"])
        self.assertIn("listed twice", texts)
        self.assertIn("Same New Sequence", texts)
        conn = db.get_connection()
        conn.execute("DELETE FROM sfa_routes")
        conn.commit()
        conn.close()
        self.assertTrue(any("route list yet" in x["text"] for x in SR.read_workbook(blank)[0]["problems"]))

    def test_route_code_from_file_name_and_chimera_format(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Lake upto Hill"
        ws.append(["Outlet Code", "Outlet Name", "New Sequence", "Top 15"])
        ws.append(["OUT/2/001", "X", 1, "Y"])
        p = os.path.join(tempfile.mkdtemp(), "07_Lake_upto_Hill_ROUI0102.xlsx")
        wb.save(p)
        self.assertEqual(SR.read_workbook(p)[0]["route_code"], "ROUI0102")
        # Chimera's own: route kept in a hidden sheet; one tab per route
        wb = openpyxl.Workbook()
        a = wb.active
        a.title = "Town"
        a.append(["Outlet Code", "Outlet Name", "New Sequence", "Top 15"])
        a.append(["OUT/1/001", "A", 1, ""])
        b = wb.create_sheet("Lake")
        b.append(["Outlet Code", "Outlet Name", "New Sequence", "Top 15"])
        b.append(["OUT/2/001", "X", 1, "✓"])
        h = wb.create_sheet("_chimera")
        for k, v in (("format", "chimera"), ("route_code:Town", "ROUI0101"), ("route_code:Lake", "ROUI0102")):
            h.append([k, v])
        h.sheet_state = "hidden"
        p = os.path.join(tempfile.mkdtemp(), "Testland.xlsx")
        wb.save(p)
        got = SR.read_workbook(p)
        self.assertEqual([(g["format"], g["route_code"], g["route_name"]) for g in got],
                         [("chimera", "ROUI0101", "Town upto Lake"), ("chimera", "ROUI0102", "Lake upto Hill")])

    def test_view_route_export(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        for line in (["Lakmee Exports Lanka Company (Pvt) Ltd"], [], ["Route"], [],
                     ["REGION", "AREA", "SUPERVISOR AREA", "TERRITORY", "ROUTE CODE", "ROUTE", "VISIT FREQUENCY", "STATUS"],
                     ["R", "A", "S", "Testland", "ROUI0101", "Town upto Lake", 1, "ACTIVE"],
                     ["R", "A", "S", "Testland", "ROUI0103", "New Road", 1, "ACTIVE"],
                     ["R", "A", "S", "Testland", "ROUI0104", "Closed Road", 1, "INACTIVE"]):
            ws.append(line)
        p = os.path.join(tempfile.mkdtemp(), "Route.xlsx")
        wb.save(p)
        rows = SR.read_routes_excel(p)
        self.assertEqual([r["code"] for r in rows], ["ROUI0101", "ROUI0103"])
        res = SS.apply_routes(rows, complete=True)
        self.assertEqual((res["added"], len(res["gone"])), (["New Road (ROUI0103)"], 2))
        self.assertFalse(SS.get_route("ROUI0102")["active"])
        bad = rep_sheet([("OUT/2/001", "X", 1, "", "", "")], code="ROUI0102", route="Lake upto Hill")
        self.assertTrue(any("no longer active" in x["text"] for x in SR.read_workbook(bad)[0]["problems"]))

    # ---------------------------------------------------------------- preview rules
    def _sheet(self):
        p = rep_sheet([("OUT/1/001", "A", 3, "✓", "", ""), ("OUT/1/002", "B", 1, "", "Closed Sundays", ""),
                       ("OUT/1/003", "C", "", "", "", ""), ("OUT/1/004", "D", 2, 1, "", ""),
                       ("OUT/1/005", "E", "", "", "", ""), ("OUT/9/999", "Gone", 4, "", "", "")])
        res = SQ.add_files([p])
        self.assertTrue(res[0]["ok"], res)
        return res[0]["sheet"], p

    def test_preview_order_and_visit_states(self):
        sid, p = self._sheet()
        self.assertFalse(SQ.add_files([p])[0]["ok"])                                    # same file again: not added twice
        s = SS.build_preview(sid, [sfa("OUT/1/001", 5), sfa("OUT/1/002", 4, "Mandatory"), sfa("OUT/1/003", 9),
                                   sfa("OUT/1/004", 1), sfa("OUT/1/005", 2, "Mandatory"), sfa("OUT/1/006", 3)])
        order = [(l["new_seq"], l["outlet_code"]) for l in s["lines"] if l["new_seq"]]
        # rep's numbers 1,2,3 → then blanks in SFA's order (005 was 2nd, 003 was 9th) → then SFA-only outlet 006
        self.assertEqual(order, [(1, "OUT/1/002"), (2, "OUT/1/004"), (3, "OUT/1/001"), (4, "OUT/1/005"), (5, "OUT/1/003"),
                                 (6, "OUT/1/006")])
        st = {l["outlet_code"]: (l["new_state"], l["state_by"]) for l in s["lines"]}
        self.assertEqual(st["OUT/1/001"], ("Mandatory", "top"))
        self.assertEqual(st["OUT/1/004"], ("Mandatory", "top"))
        self.assertEqual(st["OUT/1/002"], ("Mandatory", ""))                            # not top: kept as in SFA
        self.assertEqual(st["OUT/1/003"], ("Optional", ""))
        gone = next(l for l in s["lines"] if l["outlet_code"] == "OUT/9/999")
        self.assertEqual((gone["in_sfa"], gone["outcome"]), (False, "not_found"))
        texts = " ".join(x["text"] for x in s["problems"])
        self.assertIn("aren't on this route in SFA", texts)
        self.assertIn("not in the sheet", texts)
        self.assertEqual(s["summary"]["top"], 2)
        ids = {l["outlet_code"]: l["id"] for l in s["lines"]}
        SS.set_sheet(sid, state="ready")
        self.assertTrue(SS.set_states(sid, [ids["OUT/1/001"], ids["OUT/1/003"]], "Optional")[0])
        self.assertTrue(SS.set_states(sid, [ids["OUT/1/002"]], "Optional")[0])
        self.assertFalse(SS.set_states(sid, [ids["OUT/1/002"]], "Sometimes")[0])
        s = SS.build_preview(sid, [sfa("OUT/1/001", 5), sfa("OUT/1/002", 4, "Mandatory"), sfa("OUT/1/003", 9),
                                   sfa("OUT/1/004", 1), sfa("OUT/1/005", 2, "Mandatory"), sfa("OUT/1/006", 3)])
        st = {l["outlet_code"]: (l["new_state"], l["state_by"]) for l in s["lines"]}
        self.assertEqual(st["OUT/1/001"], ("Optional", "you"))                         # your choice survives a re-read
        SS.set_states(sid, [ids["OUT/1/001"]], "reset")
        self.assertEqual(next(l for l in SS.get_sheet(sid)["lines"] if l["outlet_code"] == "OUT/1/001")["new_state"], "Mandatory")
        ch = {l["outlet_code"] for l in SS.changes(sid)}
        self.assertNotIn("OUT/9/999", ch)
        self.assertIn("OUT/1/002", ch)                                                  # 4 → 1 and Mandatory → Optional
        # approval
        self.assertTrue(SS.approve(sid)[0])
        self.assertFalse(SS.approve(sid)[0])
        self.assertEqual(SS.get_sheet(sid, lines=False)["state"], "approved")

    def test_outcomes_newer_sheet_and_discard(self):
        sid, p = self._sheet()
        SS.build_preview(sid, [sfa("OUT/1/001", 5), sfa("OUT/1/002", 4), sfa("OUT/1/003", 9), sfa("OUT/1/004", 1),
                               sfa("OUT/1/005", 2)])
        SS.set_sheet(sid, state="ready")
        todo = SS.changes(sid)
        SS.set_line(todo[0]["id"], outcome="updated")
        SS.set_line(todo[1]["id"], outcome="failed", message="SFA error")
        self.assertEqual(len(SS.changes(sid)), len(todo) - 1)                          # a saved outlet isn't saved twice
        self.assertEqual(SS.finish_sheet(sid, False, None)[0], "partial")
        # a newer sheet for the same route replaces one still waiting
        p2 = rep_sheet([("OUT/1/001", "A", 1, "", "", "")], name="again_ROUI0101.xlsx")
        old = SQ.add_files([rep_sheet([("OUT/1/002", "B", 1, "", "", "")], name="x_ROUI0101.xlsx")])[0]["sheet"]
        new = SQ.add_files([p2])[0]["sheet"]
        self.assertEqual(SS.get_sheet(old, lines=False)["state"], "superseded")
        self.assertEqual(SS.get_sheet(sid, lines=False)["state"], "partial")           # applied ones are kept
        self.assertTrue(SS.discard(new)[0])
        self.assertFalse(SS.discard(sid)[0])

    def test_never_stuck_on_starting(self):
        import time
        # nothing waiting: refused up front, the screen is never told a run started
        r = SQ.start_background(trigger="manual")
        self.assertFalse(r["ok"])
        self.assertIn("No route sheet is waiting", r["message"])
        self.assertFalse(SQ.LIVE.snapshot().get("active"))
        # a run that crashes still ends on screen
        sid, _p = self._sheet()
        orig = SQ.run
        SQ.run = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertTrue(SQ.start_background(trigger="manual")["ok"])
            for _ in range(100):
                if not SQ.LIVE.snapshot().get("active"):
                    break
                time.sleep(0.02)
        finally:
            SQ.run = orig
        snap = SQ.LIVE.snapshot()
        self.assertFalse(snap["active"])
        self.assertIn("boom", snap["result"]["message"])

    def test_activity_record(self):
        import activity
        sid, _p = self._sheet()
        SS.build_preview(sid, [sfa("OUT/1/001", 5), sfa("OUT/1/002", 4), sfa("OUT/1/003", 9), sfa("OUT/1/004", 1),
                               sfa("OUT/1/005", 2)])
        SS.set_sheet(sid, state="ready")
        SS.approve(sid)
        for l in SS.changes(sid):
            SS.set_line(l["id"], outcome="updated", done_at=l["id"] and SS._now())
        SS.finish_sheet(sid, False, None)
        ov = activity.overview(None, None, "outlets")
        titles = [e["title"] for e in ov["events"]]
        self.assertTrue(any(t.startswith("Rep sheet received") for t in titles))
        self.assertTrue(any(t.startswith("Approved by you") for t in titles))
        self.assertTrue(any(t.startswith("Saved in SFA") for t in titles))
        self.assertEqual(ov["kpis"]["outlets_saved"], len([r for r in ov["outlets"] if r["outcome"] == "updated"]))
        self.assertTrue(any(r["outlet_code"] == "OUT/1/001" and r["new_state"] == "Mandatory" for r in ov["outlets"]))
        self.assertIn("OUT/1/002", activity.detail("sheet", sid))


if __name__ == "__main__":
    unittest.main()
