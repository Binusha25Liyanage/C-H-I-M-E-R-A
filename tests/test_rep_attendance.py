"""
Tests for Rep Attendance: reading SFA's export (made-up files only), keeping
your corrections, who hasn't logged in, history, both image looks, the run
(read -> build -> wait for approval / send) without a browser, and the
schedule wiring.

Run:  py -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

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
import schedule_store as SS  # noqa: E402
import outlet_store  # noqa: E402
import rep_attendance as R  # noqa: E402
import fake_attendance as F  # noqa: E402

DAY = "2026-11-12"


def fresh_file(name, day=DAY, **kw):
    return F.write_export(os.path.join(_TMP_HOME, name), day, **kw)


class RepAttendanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        SS.init_schema()
        outlet_store.init_schema()
        R.init_schema()

    def setUp(self):
        conn = db.get_connection()
        for t in ("att_rows", "att_days", "att_runs", "ou_territories"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()

    def test_reads_attendance_times_not_asm(self):
        p = R.read_excel(fresh_file("a.xlsx"))
        self.assertEqual(p["day"], DAY)
        self.assertEqual(len(p["rows"]), 4)
        beta = next(r for r in p["rows"] if r["territory"] == "Beta Bay")
        self.assertEqual((beta["in_time"], beta["out_time"]), ("08:05:00", "17:20:44"))   # ATTENDANCE, not ASM ATTENDANCE
        self.assertEqual(beta["asm"], "ASM Test Person")
        alpha = next(r for r in p["rows"] if r["territory"] == "Alpha Town")
        self.assertEqual(alpha["out_time"], "")

    def test_rejects_other_files_and_ranges(self):
        with self.assertRaises(R.ReadError):
            R.read_excel(fresh_file("b.xlsx", date_to="2026-11-13"))
        import openpyxl
        wb = openpyxl.Workbook(); wb.active.append(["Something", "else"]); bad = os.path.join(_TMP_HOME, "bad.xlsx"); wb.save(bad)
        with self.assertRaises(R.ReadError):
            R.read_excel(bad)

    def test_groups_and_not_logged_in(self):
        for t in ("Alpha Town", "Beta Bay", "Epsilon Field", "Zeta Point"):
            outlet_store.add_territory(t)
        R.save_day(R.read_excel(fresh_file("c.xlsx")), "sfa")
        d = R.get_day(DAY)
        self.assertEqual(d["counts"]["logged_in"], 4)
        self.assertEqual(d["counts"]["logged_out"], 2)
        self.assertEqual(d["counts"]["not_logged_out"], 2)
        self.assertEqual(sorted(d["not_logged_in"]), ["Epsilon Field", "Zeta Point"])
        self.assertEqual(d["counts"]["working"], 4)

    def test_correction_kept_until_sfa_changes(self):
        R.save_day(R.read_excel(fresh_file("d.xlsx")), "sfa")
        ok, _ = R.edit_row(DAY, "Alpha Town", in_time="7:30")
        self.assertTrue(ok)
        R.save_day(R.read_excel(fresh_file("d2.xlsx")), "sfa")          # same SFA values: correction stays
        a = next(r for r in R.get_day(DAY)["rows"] if r["territory"] == "Alpha Town")
        self.assertEqual(a["in_time"], "07:30:00")
        self.assertTrue(a["edited"])
        reps = [list(r) for r in F.REPS]
        reps[0][3] = "07:45:00"                                         # SFA's own value changed: SFA wins
        R.save_day(R.read_excel(fresh_file("d3.xlsx", reps=[tuple(r) for r in reps])), "sfa")
        a = next(r for r in R.get_day(DAY)["rows"] if r["territory"] == "Alpha Town")
        self.assertEqual(a["in_time"], "07:45:00")

    def test_edit_validation_and_undo(self):
        R.save_day(R.read_excel(fresh_file("e.xlsx")), "sfa")
        self.assertFalse(R.edit_row(DAY, "Beta Bay", in_time="nonsense")[0])
        self.assertFalse(R.edit_row(DAY, "Beta Bay", out_time="06:00")[0])          # before logging in
        self.assertFalse(R.edit_row(DAY, "Nowhere", in_time="08:00")[0])
        R.edit_row(DAY, "Beta Bay", out_time="")
        b = next(r for r in R.get_day(DAY)["rows"] if r["territory"] == "Beta Bay")
        self.assertEqual(b["group"], "not_logged_out")
        R.undo_edit(DAY, "Beta Bay")
        b = next(r for r in R.get_day(DAY)["rows"] if r["territory"] == "Beta Bay")
        self.assertEqual((b["out_time"], b["edited"]), ("17:20:44", False))

    def test_territory_gone_from_sfa_is_removed(self):
        R.save_day(R.read_excel(fresh_file("f.xlsx")), "sfa")
        R.save_day(R.read_excel(fresh_file("f2.xlsx", reps=F.REPS[:2])), "sfa")
        self.assertEqual(len(R.get_day(DAY)["rows"]), 2)

    def test_history_summary_and_export(self):
        R.save_day(R.read_excel(fresh_file("g1.xlsx", day="2026-11-10")), "sfa")
        R.save_day(R.read_excel(fresh_file("g2.xlsx", day="2026-11-11", reps=F.REPS[:3])), "sfa")
        h = R.history("2026-11-10", "2026-11-11")
        self.assertEqual(len(h["days"]), 2)
        delta = next(s for s in h["summary"] if s["territory"] == "Delta Port")
        self.assertEqual((delta["days"], delta["missing_days"]), (1, 1))
        self.assertEqual(len(R.history("2026-11-10", "2026-11-11", "gamma")["rows"]), 2)
        res = R.export_history("2026-11-10", "2026-11-11", folder=_TMP_HOME)
        path = res["path"] if isinstance(res, dict) else res[1] if isinstance(res, tuple) else res
        self.assertTrue(os.path.exists(path))

    def test_images_both_looks(self):
        import rep_attendance_images as RI
        R.save_day(R.read_excel(fresh_file("h.xlsx")), "sfa")
        info = R.get_day(DAY); info["as_of"] = "18:30"
        for style in ("classic", "professional"):
            out = RI.build(DAY, info, style, os.path.join(_TMP_HOME, "img"))
            self.assertEqual([i["group"] for i in out["images"]], ["not_logged_out", "logged_out"])
            for i in out["images"]:
                self.assertTrue(os.path.getsize(i["path"]) > 5000)
        self.assertEqual(RI.short_rep("CSR Alpha Town - Kamal Test", "Alpha Town"), "Kamal Test")

    def test_run_from_file_waits_for_approval(self):
        import rep_attendance_automation as RA
        AS.save_automation_settings({"att_style": "both", "att_send_style": "professional", "att_chats": '["Test Group"]'})
        st = RA.settings()
        rid = R.start_run(DAY, "schedule", "18:30")
        RA._after_download(rid, DAY, fresh_file("i.xlsx"), st, review=True, send_now=True, source="sfa")
        run = R.get_run(rid)
        self.assertEqual(run["state"], "pending_review")
        self.assertEqual(run["style"], "professional")
        self.assertEqual({i["style"] for i in run["images"]}, {"classic", "professional"})
        # wrong day in the file: nothing saved
        rid2 = R.start_run("2026-11-13", "manual")
        RA._after_download(rid2, "2026-11-13", fresh_file("j.xlsx"), st, review=False, send_now=False, source="sfa")
        self.assertEqual(R.get_run(rid2)["status"], "failed")
        self.assertEqual(R.get_day("2026-11-13")["rows"], [])

    def test_import_and_rebuild(self):
        import rep_attendance_automation as RA
        AS.save_automation_settings({"att_style": "classic"})
        res = RA.import_file(fresh_file("k.xlsx"))
        self.assertTrue(res["ok"])
        self.assertEqual(res["day"], DAY)
        run = R.get_run(res["run_id"])
        self.assertEqual({i["style"] for i in run["images"]}, {"classic"})
        self.assertFalse(RA.rebuild("2026-01-01")["ok"])

    def test_settings_and_schedule_type(self):
        ok, _msg = AS.save_automation_settings({"att_chats": "not json"})
        self.assertFalse(ok)
        self.assertIn("rep_attendance", SS.JOB_TYPES)
        self.assertEqual(SS.TYPE_SWITCH["rep_attendance"], "auto_rep_attendance")


if __name__ == "__main__":
    unittest.main()
