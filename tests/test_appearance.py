"""
Tests for Settings › Customize Appearance: the saved looks (splash, soundtrack, volume, logo glitch, heartbeat
line), their checks, and that every soundtrack in the list has its file.

Run:  py -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

_TMP_HOME = tempfile.mkdtemp()
os.environ.setdefault("HOME", _TMP_HOME)

import db  # noqa: E402
if not db.DB_PATH.startswith(tempfile.gettempdir()):
    db.APP_DATA_DIR = os.path.join(_TMP_HOME, "Chimera", "data")
    db.DB_PATH = os.path.join(db.APP_DATA_DIR, "chimera.db")
import automation_store as AS  # noqa: E402

SOUNDS_DIR = os.path.join(HERE, "..", "frontend", "assets", "sounds")
LOOK_KEYS = ("look_splash", "look_sound", "look_volume", "look_logo", "look_logo_freq", "look_ecg")


class AppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()

    def setUp(self):
        conn = db.get_connection()
        conn.execute("DELETE FROM automation_settings WHERE key LIKE 'look_%'")
        conn.commit(); conn.close()

    def test_defaults(self):
        look = AS.get_appearance()
        self.assertEqual({k: look[k] for k in LOOK_KEYS},
                         {"look_splash": "displacement", "look_sound": "c", "look_volume": "70",
                          "look_logo": "neon", "look_logo_freq": "rare", "look_ecg": "live"})
        self.assertEqual([s["key"] for s in look["sounds"]], list("abcdefgh"))

    def test_save_and_read_back(self):
        ok, _ = AS.save_automation_settings({"look_splash": "glitch", "look_sound": "none", "look_volume": "0",
                                             "look_logo": "off", "look_logo_freq": "often", "look_ecg": "still"})
        self.assertTrue(ok)
        look = AS.get_appearance()
        self.assertEqual((look["look_splash"], look["look_sound"], look["look_volume"], look["look_logo"],
                          look["look_logo_freq"], look["look_ecg"]), ("glitch", "none", "0", "off", "often", "still"))

    def test_bad_values_are_refused_and_nothing_changes(self):
        for k, v in (("look_splash", "spinning"), ("look_sound", "z"), ("look_volume", "150"), ("look_volume", "loud"),
                     ("look_logo", "rainbow"), ("look_logo_freq", "always"), ("look_ecg", "fast")):
            ok, message = AS.save_automation_settings({k: v})
            self.assertFalse(ok, f"{k}={v} should be refused")
            self.assertTrue(message)
        self.assertEqual(AS.get_appearance()["look_splash"], "displacement")

    def test_choices_are_case_insensitive(self):
        ok, _ = AS.save_automation_settings({"look_sound": "F", "look_ecg": "Calm"})
        self.assertTrue(ok)
        look = AS.get_appearance()
        self.assertEqual((look["look_sound"], look["look_ecg"]), ("f", "calm"))

    def test_every_soundtrack_has_its_file(self):
        for k in AS.LOOK_SOUNDS:
            path = os.path.join(SOUNDS_DIR, f"chimera-splash-{k}.mp3")
            self.assertTrue(os.path.isfile(path), path)
            self.assertGreater(os.path.getsize(path), 20000)


if __name__ == "__main__":
    unittest.main()
