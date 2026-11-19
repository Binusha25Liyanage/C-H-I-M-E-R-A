"""
Auto add loadings (loading_auto.py): SFA's suggested quantities or the whole stock, territories with nothing
suggested are paused and asked about once, SFA's rep rule is reported (not a Chimera error), and after adding:
confirm automatically / ask first / don't confirm — confirming only the loading Chimera just added.
SFA is faked (no browser). Made-up distributors only.

Run:  py -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
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
import loading_store as LS  # noqa: E402
import loading_automation as LA  # noqa: E402
import loading_create as LC  # noqa: E402
import loading_auto as LAU  # noqa: E402
import schedule_store as SS  # noqa: E402


def dist(name):
    filters = [{"label": "Territory", "type": "dropdown", "value": f"{name} TERRITORY"},
               {"label": "Distributor", "type": "dropdown", "value": name},
               {"label": "Date From", "type": "date", "date_mode": "today", "value": ""}]
    ok, msg, d = LS.save_distributor({"name": name, "frequency": "daily", "filters": filters})
    assert ok, msg
    return d


def prod(code, stock, suggested):
    return {"name": code, "code": code, "price": 10.0, "stock": stock, "last_unloaded": 0, "suggested": suggested,
            "key": f"{code}@10.0000"}


FORMS = {   # what SFA's Add Loading form shows per distributor
    "Alpha": [prod("P1", 50, 12), prod("P2", 30, 0)],
    "Beta": [prod("P1", 20, 0), prod("P2", 10, 0)],                 # SFA suggests nothing -> paused
    "Gamma": [prod("P1", 5, 5)],
}


class AutoLoadingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        LS.init_schema()
        SS.init_schema()

    def setUp(self):
        conn = db.get_connection()
        for t in ("distributors", "loading_items", "loading_runs", "loading_holds"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()
        self.ds = {n: dist(n) for n in ("Alpha", "Beta", "Gamma")}
        self.created = []
        self.prepared = []
        self.confirmed = []
        self.refuse = set()

    def _read(self, did, fields=None, headless=None):
        d = LS.get_distributor(did)
        return {"ok": True, "products": FORMS[d["name"]]}

    def _create(self, did, qty, fields=None, dry_run=None, headless=None):
        d = LS.get_distributor(did)
        if d["name"] in self.refuse:
            return {"ok": False, "blocked": True, "message": "Previously loaded quantities are not confirmed by the sales rep"}
        self.created.append((d["name"], dict(qty)))
        return {"ok": True, "saved": True, "number": f"LD-{d['name']}"}

    def _prepare(self, ids, trigger="manual", headless=None):
        rid = LS.start_run("prepare", trigger)
        for i in ids:
            d = LS.get_distributor(i)
            # View Loadings shows one pending loading per distributor; for Gamma it's an older one, not Chimera's
            LS.add_item(rid, d, "pending_approval", loading_number="LD-OLD" if d["name"] == "Gamma" else f"LD-{d['name']}")
        self.prepared.append(list(ids))
        LS.finish_run(rid, "done", "")
        return {"ok": True, "run_id": rid}

    def _confirm(self, item_ids, trigger="manual", headless=None):
        self.confirmed += [LS.get_item(i)["loading_number"] for i in item_ids]
        return {"ok": True, "message": f"{len(item_ids)} confirmed."}

    def run_job(self, names, mode="suggested", after="ask"):
        with mock.patch.object(LC, "read_form", self._read), mock.patch.object(LC, "create", self._create), \
                mock.patch.object(LA, "run_prepare", self._prepare), mock.patch.object(LA, "run_confirm", self._confirm), \
                mock.patch.object(LAU, "_note"):
            return LAU.add_all([self.ds[n]["id"] for n in names], mode, after, job_name="Monday loadings")

    def test_suggested_quantities_and_nothing_suggested_is_paused(self):
        r = self.run_job(["Alpha", "Beta", "Gamma"], after="none")
        self.assertEqual(sorted(r["added"]), ["Alpha", "Gamma"])
        self.assertEqual(self.created[0], ("Alpha", {"P1@10.0000": 12}))      # only what SFA suggested
        self.assertEqual(r["paused"], ["Beta"])
        holds = LS.list_holds()
        self.assertEqual([(h["distributor_name"], h["stock_total"]) for h in holds], [("Beta", 30)])
        self.assertEqual(self.prepared, [])                                    # "don't confirm": nothing more

    def test_whole_stock(self):
        self.run_job(["Alpha", "Beta"], mode="stock", after="none")
        self.assertEqual(dict(self.created)["Alpha"], {"P1@10.0000": 50, "P2@10.0000": 30})
        self.assertEqual(dict(self.created)["Beta"], {"P1@10.0000": 20, "P2@10.0000": 10})
        self.assertEqual(LS.list_holds(), [])

    def test_auto_confirm_only_what_chimera_added(self):
        self.run_job(["Alpha", "Gamma"], after="auto")
        self.assertEqual(self.confirmed, ["LD-Alpha"])           # Gamma's pending one isn't the one Chimera added

    def test_ask_first_leaves_them_waiting(self):
        r = self.run_job(["Alpha"], after="ask")
        self.assertEqual(self.confirmed, [])
        self.assertIn("waiting for your approval", r["message"])

    def test_rep_rule_is_reported_and_the_rest_go_on(self):
        self.refuse = {"Alpha"}
        r = self.run_job(["Alpha", "Gamma"], after="none")
        self.assertEqual(r["added"], ["Gamma"])
        self.assertIn("SFA's rule", r["failed"][0][1])

    def test_approving_a_pause_loads_the_whole_stock(self):
        self.run_job(["Beta"], after="auto")
        h = LS.list_holds()[0]
        with mock.patch.object(LAU, "start_background",
                               lambda ids, mode, after, trigger, job_name, hold_ids:
                               self.run_job_direct(ids, mode, after, hold_ids)):
            LAU.approve_holds([h["id"]])
        self.assertEqual(dict(self.created)["Beta"], {"P1@10.0000": 20, "P2@10.0000": 10})
        self.assertEqual(LS.get_hold(h["id"])["state"], "added")
        self.assertEqual(self.confirmed, ["LD-Beta"])

    def run_job_direct(self, ids, mode, after, hold_ids):
        with mock.patch.object(LC, "read_form", self._read), mock.patch.object(LC, "create", self._create), \
                mock.patch.object(LA, "run_prepare", self._prepare), mock.patch.object(LA, "run_confirm", self._confirm), \
                mock.patch.object(LAU, "_note"):
            LAU.add_all(ids, mode, after, hold_ids=hold_ids)
        return {"ok": True}

    def test_unanswered_questions_end_with_the_day(self):
        self.run_job(["Beta"], after="ask")
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(LAU.expire_old(tomorrow), 1)
        self.assertEqual(LS.list_holds(), [])

    def test_job_options_are_checked(self):
        base = {"name": "Mon", "job_type": "auto_loadings", "frequency": "weekly", "weekdays": "1000000", "times": "07:00",
                "options": {"distributor_ids": [self.ds["Alpha"]["id"]], "quantities": "suggested", "after": "auto"}}
        ok, msg, j = SS.save_job(base)
        self.assertTrue(ok, msg)
        self.assertEqual(j["options"]["after"], "auto")
        self.assertFalse(SS.save_job({**base, "options": {**base["options"], "distributor_ids": []}})[0])
        self.assertFalse(SS.save_job({**base, "options": {**base["options"], "quantities": "half"}})[0])
        SS.delete_job(j["id"])


if __name__ == "__main__":
    unittest.main()
