"""
Tests for Windows notifications (desktop_notify) and the WhatsApp message
sent before the reports. Nothing is shown here: the notification text,
which runs notify and how, the on/off switches, and the click-to-open path.

Run:  py -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import time
import unittest
import xml.dom.minidom

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

_TMP_HOME = tempfile.mkdtemp()
os.environ["HOME"] = _TMP_HOME
os.environ["USERPROFILE"] = _TMP_HOME

import db  # noqa: E402
db.APP_DATA_DIR = os.path.join(_TMP_HOME, "Chimera", "data")
db.DB_PATH = os.path.join(db.APP_DATA_DIR, "chimera.db")
import automation_store as AS  # noqa: E402
import desktop_notify as DN  # noqa: E402
import sfa_automation as SA  # noqa: E402


class NotifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        import report_pipeline, sfa_store, whatsapp_sender
        sfa_store.init_schema()
        report_pipeline.init_schema()
        whatsapp_sender.init_schema()

    def setUp(self):
        self.shown = []
        self._orig = DN._show_now
        DN._show_now = lambda title, body, action, kind, tag: self.shown.append((kind, title, body, action)) or (True, "")
        DN._recent.clear()
        AS.save_automation_settings({k: "1" for k in ("notify_desktop", "notify_started", "notify_approval",
                                                      "notify_finished", "notify_failed")})

    def tearDown(self):
        DN._show_now = self._orig

    def wait(self, n=1):
        for _ in range(50):
            if len(self.shown) >= n:
                return
            time.sleep(0.02)

    def test_toast_xml_is_valid_and_escaped(self):
        x = DN.toast_xml('Reports <for> "Asela & Co"', "a & b < c", "preview:12", "approval")
        doc = xml.dom.minidom.parseString(x)
        self.assertEqual(doc.documentElement.getAttribute("launch"), "chimera:open?action=preview:12")
        self.assertEqual(doc.documentElement.getAttribute("activationType"), "protocol")
        texts = [t.firstChild.data for t in doc.getElementsByTagName("text")]
        self.assertEqual(texts[:2], ['Reports <for> "Asela & Co"', "a & b < c"])
        self.assertEqual(doc.getElementsByTagName("action")[0].getAttribute("content"), "Review now")

    def test_switches(self):
        self.assertTrue(DN.event("finished", "reports", "Done", "x"))
        AS.save_automation_settings({"notify_finished": "0"})
        self.assertFalse(DN.event("finished", "reports", "Done again", "x"))
        self.assertTrue(DN.event("failed", "reports", "Broke", "x"))
        AS.save_automation_settings({"notify_desktop": "0"})
        self.assertFalse(DN.event("failed", "reports", "Broke again", "x"))
        self.assertFalse(DN.event("whatever", "reports", "?", "x"))
        AS.save_automation_settings({"notify_desktop": "1"})
        self.assertTrue(DN.event("failed", "reports", "Once", "x", tag="same"))
        self.assertFalse(DN.event("failed", "reports", "Twice", "x", tag="same"))     # no repeat within seconds

    def test_live_state_notifies_start_and_end(self):
        live = SA.LiveState("loadings")
        live.reset(kind="confirm", trigger="manual")
        live.set(run_id=7)
        live.set(run_id=7)                                                             # once only
        live.set(active=False, phase="done", result={"ok": True, "status": "done", "message": "4 loading(s) confirmed."})
        self.wait(2)
        self.assertEqual([(k, t) for k, t, _b, _a in self.shown],
                         [("started", "Confirming loadings in SFA — started"), ("finished", "Confirming loadings in SFA — done")])
        self.assertEqual(self.shown[1][2], "4 loading(s) confirmed.")
        self.assertEqual(self.shown[1][3], "loadings")
        quiet = SA.LiveState()                                                         # no source: no notifications
        quiet.reset(trigger="manual")
        quiet.set(run_id=1)
        time.sleep(0.1)
        self.assertEqual(len(self.shown), 2)

    def test_scheduled_run_and_failures(self):
        live = SA.LiveState("itinerary")
        live.reset(kind="itinerary", trigger="scheduled")
        live.set(run_id=3)                                         # the scheduler already said "<job> — <time>"
        live.set(active=False, result={"ok": False, "status": "failed", "message": "Login failed."})
        self.wait(1)
        self.assertEqual([(k, t) for k, t, _b, _a in self.shown], [("failed", "Reading itineraries — problem")])
        self.shown.clear()
        busy = SA.LiveState("reports")
        busy.reset(trigger="manual")
        busy.set(active=False, result={"ok": False, "status": "busy", "message": "Another SFA run is in progress."})
        busy.reset(trigger="scheduled 12:00")
        busy.set(active=False, result={"ok": False, "status": "busy", "message": "Another SFA run is in progress."})
        self.wait(1)
        time.sleep(0.1)
        self.assertEqual([(k, t) for k, t, _b, _a in self.shown], [("failed", "SFA reports — could not start")])

    def test_reports_waiting_for_approval(self):
        live = SA.LiveState("reports")
        live.reset(trigger="scheduled", report_date="2026-11-11", slot="08:00")
        live.set(active=False, result={"ok": True, "status": "success", "message": "5 of 5 report(s) downloaded.",
                                       "report_run": {"run_id": 41, "status": "pending_review"}})
        self.wait(1)
        kind, title, body, action = self.shown[0]
        self.assertEqual((kind, action), ("approval", "preview:41"))
        self.assertIn("2026-11-11", body)

    def test_scheduler_fire(self):
        import schedule_store as SS
        import scheduler
        SS.init_schema()
        calls = []
        orig = scheduler._run_job
        scheduler._run_job = lambda job, slot, trigger: calls.append(slot) or {"ok": True, "status": "done", "message": ""}
        orig_due = scheduler.due_jobs
        scheduler.due_jobs = lambda now=None: ([({"id": 9, "name": "Daily SKU reports", "job_type": "sfa_downloads",
                                                 "type_label": "SFA downloads"}, "12:00")], "")
        orig_rec = SS.record_fire
        SS.record_fire = lambda *a: True
        orig_out = SS.set_fire_outcome
        SS.set_fire_outcome = lambda *a: None
        try:
            scheduler.run_due("scheduled")
        finally:
            scheduler._run_job, scheduler.due_jobs, SS.record_fire, SS.set_fire_outcome = orig, orig_due, orig_rec, orig_out
        self.wait(1)
        self.assertEqual(calls, ["12:00"])
        self.assertEqual(self.shown[0][:2], ("started", "Daily SKU reports — 12:00"))
        self.assertEqual(self.shown[0][3], "automation")

    def test_click_opens_screen(self):
        self.assertEqual(DN.action_from_link('"chimera:open?action=preview:12"'), "preview:12")
        self.assertEqual(DN.action_from_link("chimera:open"), "overview")
        DN.request_open("chimera:open?action=loadings-approve")
        self.assertEqual(DN.take_open_request(), "loadings-approve")
        self.assertIsNone(DN.take_open_request())                                      # used once
        self.assertIn("--open", DN.launch_command())


class PreMessageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        import whatsapp_sender
        whatsapp_sender.init_schema()

    def test_rule_and_plan(self):
        import whatsapp_sender as W
        from datetime import datetime
        self.assertEqual(W.fill_message("Reports for {date} — {day} {time}", "2026-11-11", datetime(2026, 11, 12, 8, 5)),
                         "Reports for 2026-11-11 — Thursday 08:05")
        ok, _ = W.save_rule({"chat_name": "Team A", "kinds": ["sku"], "groups": ["*"], "caption": "",
                             "pre_message": "  Good morning,\r\nreports for {date}  "})
        self.assertTrue(ok)
        ok, msg = W.save_rule({"chat_name": "Team B", "kinds": ["sku"], "groups": ["*"], "pre_message": "x" * 2001})
        self.assertFalse(ok)
        rules = {r["chat_name"]: r for r in W.list_rules()}
        self.assertEqual(rules["Team A"]["pre_message"], "Good morning,\nreports for {date}")
        W.save_rule({"chat_name": "Team C", "kinds": ["sku"], "groups": ["*"]})
        rules = {r["chat_name"]: r for r in W.list_rules()}
        self.assertEqual(rules["Team C"]["pre_message"], "")
        plan = {p["chat_name"]: p for p in W.plan_delivery({"report_date": "2026-11-11", "summary": {"reports": []}},
                                                           [rules["Team A"], rules["Team C"]])}
        self.assertEqual(plan["Team A"]["pre_message"], "Good morning,\nreports for 2026-11-11")
        self.assertEqual(plan["Team C"]["pre_message"], "")


if __name__ == "__main__":
    unittest.main()
