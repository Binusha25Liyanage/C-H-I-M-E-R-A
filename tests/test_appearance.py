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
import appearance_media as M  # noqa: E402

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
        self.tmp = tempfile.mkdtemp()

    def _file(self, name, size=4000):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(os.urandom(size))      # made-up bytes: the checks here are about names, sizes and the list
        return p

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

    def test_all_built_in_openings_can_be_chosen(self):
        for v in AS.LOOK_SPLASHES:
            ok, message = AS.save_automation_settings({"look_splash": v})
            self.assertTrue(ok, message)
        self.assertEqual(AS.get_appearance()["splashes"], list(AS.LOOK_SPLASHES))

    def test_add_a_soundtrack_choose_it_rename_and_remove(self):
        ok, message, item = M.add("sound", self._file("My Intro_Song.mp3"))
        self.assertTrue(ok, message)
        self.assertTrue(item["id"].startswith("s_"))
        self.assertEqual(item["name"], "My Intro Song")
        copy = os.path.join(M.media_dir(), item["file"])
        self.assertTrue(os.path.isfile(copy))
        self.assertTrue(copy.startswith(db.APP_DATA_DIR))          # kept in Chimera's data folder, not the project
        ok, _ = AS.save_automation_settings({"look_sound": item["id"]})
        self.assertTrue(ok)
        self.assertEqual(AS.get_appearance()["look_sound"], item["id"])
        self.assertTrue(M.update(item["id"], name="  Boss   theme ")[0])
        self.assertEqual(AS.get_appearance()["custom"][0]["name"], "Boss theme")
        ok, _ = M.delete(item["id"])
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(copy))
        look = AS.get_appearance()
        self.assertEqual(look["look_sound"], "c")                  # the default comes back
        self.assertEqual(look["custom"], [])

    def test_add_a_video_and_its_transition(self):
        ok, _, item = M.add("video", self._file("opening.mp4"))
        self.assertTrue(ok)
        self.assertEqual(item["transition"], "displacement")
        self.assertTrue(AS.save_automation_settings({"look_splash": item["id"]})[0])
        self.assertTrue(M.update(item["id"], transition="fade")[0])
        self.assertFalse(M.update(item["id"], transition="explode")[0])
        self.assertEqual(AS.get_appearance()["custom"][0]["transition"], "fade")
        M.delete(item["id"])

    def test_wrong_files_are_refused(self):
        self.assertFalse(M.add("sound", self._file("notes.txt"))[0])
        self.assertFalse(M.add("video", self._file("song.mp3"))[0])
        self.assertFalse(M.add("sound", self._file("empty.mp3", 0))[0])
        self.assertFalse(M.add("sound", os.path.join(self.tmp, "missing.mp3"))[0])
        self.assertEqual(AS.look_custom(), [])

    def test_unknown_custom_ids_cannot_be_chosen(self):
        self.assertFalse(AS.save_automation_settings({"look_sound": "s_deadbeef"})[0])
        self.assertFalse(AS.save_automation_settings({"look_splash": "v_deadbeef"})[0])

    def test_a_missing_file_falls_back_to_the_default(self):
        ok, _, item = M.add("video", self._file("gone.webm"))
        AS.save_automation_settings({"look_splash": item["id"]})
        os.remove(os.path.join(M.media_dir(), item["file"]))
        self.assertEqual(AS.get_appearance()["look_splash"], "displacement")
        self.assertFalse(M.media_info(item["id"])["ok"])
        M.delete(item["id"])

    def test_reading_a_file_in_pieces_gives_it_back_whole(self):
        import base64
        data_path = self._file("long.wav", M.CHUNK * 2 + 123)
        ok, _, item = M.add("sound", data_path)
        info = M.media_info(item["id"])
        self.assertEqual(info["chunks"], 3)
        self.assertTrue(info["url"].startswith("file:"))
        got = b"".join(base64.b64decode(M.media_chunk(item["id"], i)["data"]) for i in range(info["chunks"]))
        with open(data_path, "rb") as f:
            self.assertEqual(got, f.read())
        M.delete(item["id"])

    def test_every_soundtrack_has_its_file(self):
        for k in AS.LOOK_SOUNDS:
            path = os.path.join(SOUNDS_DIR, f"chimera-splash-{k}.mp3")
            self.assertTrue(os.path.isfile(path), path)
            self.assertGreater(os.path.getsize(path), 20000)


if __name__ == "__main__":
    unittest.main()
