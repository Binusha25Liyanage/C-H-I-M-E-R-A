"""
Desktop icon glitch (desktop_icon.py): the right shortcuts are picked, a glitch always ends on the normal icon
(even if something fails half-way), the frames ship with Chimera, and the settings are checked.
No Windows needed: PowerShell is faked.

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
import desktop_icon as DI  # noqa: E402


class FakeShell:
    def __init__(self, lines=(), fail_on=None):
        self.lines, self.cmds, self.fail_on = list(lines), [], fail_on

    def run(self, cmd, timeout=10):
        self.cmds.append(cmd)
        if self.fail_on and self.fail_on in cmd:
            raise RuntimeError("boom")
        return self.lines if "GetFolderPath" in cmd else []


class DesktopIconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        AS.init_schema()

    def setUp(self):
        from unittest import mock
        p = mock.patch.object(DI.time, "sleep", lambda s: None)   # only during each test (time is shared!)
        p.start()
        self.addCleanup(p.stop)

    def test_frames_ship_with_chimera_and_sequence_ends_normal(self):
        for f in DI.FRAME_FILES:
            self.assertTrue(os.path.isfile(os.path.join(DI._assets_dir(), f)), f)
        self.assertEqual(DI.SEQUENCE[-1][0], "normal.ico")
        names = [f for f, _ in DI.SEQUENCE]
        for look in ("neon.ico", "dark.ico"):                      # all three looks, a glitch before each
            self.assertIn(look, names)
            self.assertTrue(names[names.index(look) - 1].endswith("_3.ico"))
        self.assertTrue(all(ms >= 150 for f, ms in DI.SEQUENCE if "_to_" in f))   # slow enough for Explorer
        folder = DI.frames_dir()                            # copied to a stable place
        self.assertTrue(all(os.path.isfile(os.path.join(folder, f)) for f in DI.FRAME_FILES))

    def test_only_chimera_shortcuts_are_touched(self):
        root = r"D:\projects\Project C H I M E R A"
        sh = FakeShell([
            r"C:\Users\x\Desktop\Chimera.lnk|D:\projects\Project C H I M E R A\Run Chimera.bat|D:\projects\Project C H I M E R A\frontend\assets\chimera.ico,0",
            r"C:\Users\x\Desktop\Chrome.lnk|C:\Program Files\Google\Chrome\chrome.exe|",
            r"C:\Users\x\Desktop\Old Chimera.lnk|D:\old\Chimera.exe|",
            r"C:\Users\x\Desktop\Projects.lnk|D:\projects|",
        ])
        found = DI.find_shortcuts(sh, root)
        self.assertEqual([os.path.basename(f.replace("\\", "/")) for f in found], ["Chimera.lnk", "Old Chimera.lnk"])

    def test_glitch_ends_on_the_normal_icon(self):
        sh = FakeShell()
        self.assertTrue(DI.glitch_once(sh, ["C:/Desktop/Chimera.lnk"], "F"))
        icons = [c.split("IconLocation = ")[1].split(";")[0] for c in sh.cmds if "IconLocation" in c]
        self.assertTrue(any("neon.ico" in i for i in icons) and any("dark.ico" in i for i in icons))
        self.assertTrue(icons[-1].endswith("normal.ico,0'"))

    def test_a_failure_half_way_still_puts_the_normal_icon_back(self):
        sh = FakeShell(fail_on="neon_to_dark_2.ico")
        with self.assertRaises(RuntimeError):
            DI.glitch_once(sh, ["C:/Desktop/Chimera.lnk"], "F")
        self.assertTrue(sh.cmds[-1].count("normal.ico"))

    def test_settings_are_checked(self):
        self.assertTrue(AS.save_automation_settings({"look_icon": "always", "look_icon_every": "60"})[0])
        self.assertEqual(DI.settings(), ("always", 60))
        self.assertFalse(AS.save_automation_settings({"look_icon": "sometimes"})[0])
        self.assertFalse(AS.save_automation_settings({"look_icon_every": "3"})[0])
        self.assertTrue(AS.save_automation_settings({"look_icon": "open", "look_icon_every": "30"})[0])
        look = AS.get_appearance()
        self.assertEqual((look["look_icon"], look["look_icon_every"]), ("open", "30"))

    def test_helper_command_and_quoting(self):
        self.assertIn("--helper", DI.helper_command())
        self.assertEqual(DI._ps_quote("D:\\it's"), "'D:\\it''s'")


if __name__ == "__main__":
    unittest.main()
