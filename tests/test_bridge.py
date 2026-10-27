"""
Guards the Python <-> screen bridge (backend/api.py), so "X is not a function"
errors are caught by the tests instead of in the app:

  * every function the screen calls (window.pywebview.api.X / API().X in the
    frontend JavaScript) must exist on Api;
  * Api must not have public non-method attributes — pywebview walks those
    when it builds window.pywebview.api, and walking into the native window or
    the scheduler thread breaks the whole bridge.

Run:  py -m unittest discover -s tests -v
"""

import glob
import os
import re
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))

_TMP_HOME = tempfile.mkdtemp()
os.environ["HOME"] = _TMP_HOME
os.environ["USERPROFILE"] = _TMP_HOME

import db  # noqa: E402
db.APP_DATA_DIR = os.path.join(_TMP_HOME, "Chimera", "data")
db.DB_PATH = os.path.join(db.APP_DATA_DIR, "chimera.db")


def _js_calls():
    names = set()
    for f in glob.glob(os.path.join(ROOT, "frontend", "js", "*.js")):
        src = open(f, encoding="utf-8").read()
        names |= set(re.findall(r"pywebview\.api\.([A-Za-z_]\w*)", src))
        names |= set(re.findall(r"\bAPI\(\)\.([A-Za-z_]\w*)", src))
        # runSfaProbe("method_name", ...) style indirect calls
        names |= set(re.findall(r"runSfaProbe\(\s*\"([A-Za-z_]\w*)\"", src))
    return names


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import webview  # noqa: F401
        except ImportError:  # the tests don't need a real window
            sys.modules["webview"] = types.SimpleNamespace(OPEN_DIALOG=0, windows=[])
        import api
        import app
        import scheduler
        cls.api = api.Api()
        # Attach things exactly as app.py does at start-up.
        cls.api._win = object()
        cls.api._scheduler = scheduler.InAppScheduler()
        cls.app = app

    def test_every_function_the_screen_calls_exists(self):
        missing = sorted(n for n in _js_calls() if not callable(getattr(self.api, n, None)))
        self.assertEqual(missing, [], "The screen calls these, but backend/api.py doesn't have them")

    def test_no_public_non_method_attributes(self):
        self.assertEqual(self.app.api_public_non_methods(self.api), [])

    def test_js_actually_calls_things(self):
        self.assertGreater(len(_js_calls()), 40)


if __name__ == "__main__":
    unittest.main()
