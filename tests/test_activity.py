"""
Tests for the Activity Monitor: it gathers what each part of Chimera
recorded (downloads, reports, WhatsApp, loadings, itineraries, the
distributor list, schedules) into one timeline, lists each loading, and
exports to Excel. No browser needed.

Run:  py -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

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
import activity  # noqa: E402

TODAY = date.today().isoformat()
YDAY = (date.today() - timedelta(days=1)).isoformat()


def at(day, hm):
    return f"{day}T{hm}:00"


class ActivityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        LS.init_schema()
        import report_pipeline, whatsapp_sender, schedule_store, outlet_store
        for m in (report_pipeline, whatsapp_sender, schedule_store, outlet_store):
            m.init_schema()
        c = db.get_connection()
        c.execute("INSERT INTO automation_runs (started_at, finished_at, report_date, trigger, slot, status, lanes, message) "
                  "VALUES (?,?,?,?,?,?,?,?)", (at(TODAY, "08:00"), at(TODAY, "08:06"), YDAY, "scheduled", "08:00", "partial",
                                               json.dumps([{"name": "A", "status": "done"}, {"name": "B", "status": "empty"}, {"name": "C", "status": "failed"}]), "1 report failed"))
        c.execute("INSERT INTO report_runs (started_at, report_date, trigger, status, folder, summary) VALUES (?,?,?,?,?,?)",
                  (at(TODAY, "08:07"), YDAY, "scheduled", "success", "C:/r",
                   json.dumps({"reports": [{"kind": "sku", "label": "SKU Report", "ok": True},
                                           {"kind": "pivot", "label": "Pivot", "ok": False, "reason": "Missing input(s): X"}]})))
        c.execute("INSERT INTO wa_deliveries (report_run_id, chat_name, files, status, message, sent_at) VALUES (1,'Sales Team',4,'sent','',?)",
                  (at(TODAY, "08:09"),))
        c.execute("INSERT INTO wa_deliveries (report_run_id, chat_name, files, status, message, sent_at) VALUES (1,'ASM Group',2,'failed','Chat not found',?)",
                  (at(TODAY, "08:10"),))
        c.execute("INSERT INTO ou_runs (kind, started_at, trigger, target_day, status, message, log) VALUES ('itinerary',?,'auto',?,'done','12 read','L')",
                  (at(YDAY, "23:59"), TODAY))
        c.execute("INSERT INTO schedule_jobs (name, job_type, frequency, times) VALUES ('Morning reports','reports','daily','08:00')")
        c.execute("INSERT INTO schedule_fires (job_id, day, slot, fired_at, trigger, outcome) VALUES (1,?,'08:00',?,'scheduled','done')",
                  (TODAY, at(TODAY, "08:00")))
        c.commit()
        c.close()
        ok, _, d = LS.save_distributor({"name": "N Nirothkanth", "frequency": "daily",
                                        "filters": [{"label": "Distributor", "type": "dropdown", "value": "N Nirothkanth"}]})
        ok, _, e = LS.save_distributor({"name": "V Pathmasiri", "frequency": "daily",
                                        "filters": [{"label": "Distributor", "type": "dropdown", "value": "V Pathmasiri"}]})
        r = LS.start_run("prepare", "schedule")
        i1 = LS.add_item(r, d, "pending_approval", loading_number="LD-1", rep="Rep A", products=[],
                         totals={"qty": "10", "amount": "1,500.00"})
        i2 = LS.add_item(r, e, "pending_approval", loading_number="LD-2", rep="Rep B", products=[],
                         totals={"qty": "4", "amount": "600.00"})
        LS.finish_run(r, "done", "2 waiting for approval", "prepare log")
        c = db.get_connection()
        c.execute("UPDATE loading_items SET state='confirmed', confirmed_at=? WHERE id=?", (at(TODAY, "10:00"), i1))
        c.execute("UPDATE loading_items SET state='changed', decided_at=?, message='qty changed' WHERE id=?", (at(TODAY, "10:00"), i2))
        c.commit()
        c.close()
        LS.record_add(d, {"number": "LD-9", "verified": True}, dry_run=False)
        LS.record_add(e, {"number": "LD-10"}, dry_run=True)
        s = LS.start_run("sync", "schedule")
        LS.finish_run(s, "done", "170 distributors", "")

    def test_timeline_has_every_process(self):
        ov = activity.overview(TODAY, TODAY)
        procs = {e["process"] for e in ov["events"]}
        self.assertEqual(procs, {"downloads", "reports", "whatsapp", "loadings", "distributors", "schedules"})  # itinerary was yesterday
        dl = next(e for e in ov["events"] if e["process"] == "downloads")
        self.assertIn("2/3 reports", dl["title"])
        self.assertEqual(dl["status"], "warn")
        rep = next(e for e in ov["events"] if e["process"] == "reports")
        self.assertEqual(rep["detail"], "SKU Report")
        wa = [e["status"] for e in ov["events"] if e["process"] == "whatsapp"]
        self.assertEqual(sorted(wa), ["fail", "ok"])
        self.assertEqual(ov["kpis"]["whatsapp_sent"], 1)
        times = [e["time"] for e in ov["events"]]
        self.assertEqual(times, sorted(times, reverse=True))                   # newest first

    def test_range_filter_and_search(self):
        ov = activity.overview(YDAY, TODAY, "itinerary")
        self.assertEqual(len(ov["events"]), 1)
        self.assertIn(f"for {TODAY}", ov["events"][0]["title"])
        self.assertEqual(len(ov["per_day"]), 2)
        self.assertEqual(ov["per_day"][0]["itinerary"], 1)
        self.assertEqual(len(activity.overview(TODAY, YDAY)["per_day"]), 2)    # reversed dates are swapped
        hits = activity.overview(TODAY, TODAY, "all", "asm group")["events"]
        self.assertEqual([e["process"] for e in hits], ["whatsapp"])

    def test_each_loading(self):
        ov = activity.overview(TODAY, TODAY, "loadings")
        by = {(r["distributor"], r["state"]): r for r in ov["loadings"]}
        self.assertEqual(by[("N Nirothkanth", "confirmed")]["amount"], 1500.0)
        self.assertEqual(by[("V Pathmasiri", "changed")]["status"], "fail")
        self.assertEqual(by[("N Nirothkanth", "added")]["loading_number"], "LD-9")
        self.assertIn(("V Pathmasiri", "dry_run"), by)
        k = ov["kpis"]
        self.assertEqual((k["loadings_confirmed"], k["loadings_amount"], k["loadings_added"]), (1, 1500.0, 1))
        self.assertTrue(all(e["process"] == "loadings" for e in ov["events"]))
        self.assertEqual(activity.overview(TODAY, TODAY, "all", "LD-2")["loadings"][0]["distributor"], "V Pathmasiri")

    def test_details(self):
        ov = activity.overview(TODAY, TODAY)
        prep = next(e for e in ov["events"] if e["title"].startswith("Loadings read"))
        txt = activity.detail("loading", prep["ref"]["id"])
        self.assertIn("V Pathmasiri", txt)
        self.assertIn("prepare log", txt)
        rep = next(e for e in ov["events"] if e["process"] == "reports")
        self.assertIn("Missing input(s): X", activity.detail("report", rep["ref"]["id"]))
        wa = next(e for e in ov["events"] if e["process"] == "whatsapp" and e["status"] == "fail")
        self.assertIn("Chat not found", activity.detail("whatsapp", wa["ref"]["id"]))

    def test_export(self):
        import openpyxl
        p = activity.export_excel(YDAY, TODAY, folder=tempfile.mkdtemp())
        wb = openpyxl.load_workbook(p)
        self.assertEqual(wb.sheetnames, ["Activity", "Loadings", "Outlet updates", "Summary"])
        self.assertEqual(wb["Activity"].max_row - 1, len(activity.overview(YDAY, TODAY)["events"]))
        names = [r[1] for r in wb["Loadings"].iter_rows(min_row=2, values_only=True)]
        self.assertIn("N Nirothkanth", names)

    def test_missing_tables_dont_break(self):
        c = db.get_connection()
        c.execute("ALTER TABLE ou_runs RENAME TO ou_runs_x")
        c.commit()
        try:
            self.assertTrue(activity.overview(YDAY, TODAY)["events"])
        finally:
            c.execute("ALTER TABLE ou_runs_x RENAME TO ou_runs")
            c.commit()
            c.close()


if __name__ == "__main__":
    unittest.main()
