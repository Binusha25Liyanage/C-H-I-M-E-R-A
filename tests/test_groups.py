"""
Tests for editing manager groups (Settings > Manager Groups): rename a
manager, move a territory, and WhatsApp delivery rules following a rename.

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
import sfa_store  # noqa: E402
import whatsapp_sender  # noqa: E402


class GroupsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()
        whatsapp_sender.init_schema()

    def setUp(self):
        conn = db.get_connection()
        conn.execute("DELETE FROM territories")
        conn.execute("DELETE FROM wa_rules")
        conn.commit()
        conn.close()
        for t in ("Chilaw", "Wennappuwa"):
            sfa_store.save_territory(t, "Ekanayake", "Van")
        for t in ("Galgamuwa", "Kurunegala"):
            sfa_store.save_territory(t, "Sudesh", "Pre")

    def test_rename_group_and_rules(self):
        whatsapp_sender.save_rule({"chat_name": "Sudesh chat", "kinds": ["sku"], "groups": ["Sudesh"], "enabled": True})
        whatsapp_sender.save_rule({"chat_name": "All", "kinds": ["sku"], "groups": ["*"], "enabled": True})
        ok, msg, merged = sfa_store.rename_group("Sudesh", "Milan")
        self.assertTrue(ok)
        self.assertFalse(merged)
        self.assertEqual(whatsapp_sender.rename_group_in_rules("Sudesh", "Milan"), 1)
        g = sfa_store.get_groups()
        self.assertNotIn("Sudesh", g)
        self.assertEqual([t for t, _ in g["Milan"]], ["Galgamuwa", "Kurunegala"])
        self.assertEqual({r["chat_name"]: r["groups"] for r in whatsapp_sender.list_rules()},
                         {"Sudesh chat": ["Milan"], "All": ["*"]})
        self.assertFalse(sfa_store.rename_group("Nobody", "X")[0])

    def test_move_territory_keeps_model(self):
        ok, msg = sfa_store.save_territory("Wennappuwa", "Sudesh", "Van")
        self.assertTrue(ok)
        self.assertIn("moved from Ekanayake to Sudesh", msg)
        g = sfa_store.get_groups()
        self.assertEqual(g["Ekanayake"], [("Chilaw", "Van")])
        self.assertIn(("Wennappuwa", "Van"), g["Sudesh"])

    def test_rename_onto_existing_merges(self):
        ok, msg, merged = sfa_store.rename_group("Sudesh", "Ekanayake")
        self.assertTrue(ok and merged)
        self.assertEqual(len(sfa_store.get_groups()["Ekanayake"]), 4)

    def test_delete_and_validation(self):
        self.assertTrue(sfa_store.delete_territory("Chilaw"))
        self.assertFalse(sfa_store.delete_territory("Chilaw"))
        self.assertFalse(sfa_store.save_territory("", "Ekanayake")[0])
        self.assertFalse(sfa_store.save_territory("X", "")[0])


if __name__ == "__main__":
    unittest.main()
