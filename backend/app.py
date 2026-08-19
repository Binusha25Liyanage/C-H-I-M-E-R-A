"""
app.py
Entry point for the desktop app. Run this with:

    python app.py

This opens one native window (no browser tab, no server URL to visit) with
the frontend/index.html page rendered inside it, and wires up the Api
bridge from api.py so JavaScript in that page can call real Python code.
"""

import os
import sys
import webview

from api import Api

APP_TITLE = "C H I M E R A"

# In a normal dev run, frontend/ sits one folder up from this file. Inside
# a PyInstaller-built exe, bundled data (added via build.py's --add-data)
# is extracted to a temp folder at sys._MEIPASS instead — so the frontend
# path has to be resolved differently depending on which situation we're
# actually running in.
if getattr(sys, "frozen", False):
    FRONTEND_DIR = os.path.join(sys._MEIPASS, "frontend")
else:
    FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")

INDEX_HTML = os.path.join(FRONTEND_DIR, "index.html")
ICON_PATH = os.path.join(FRONTEND_DIR, "assets", "chimera.ico")


def main():
    api = Api()

    window_kwargs = dict(
        js_api=api,
        width=1280,
        height=800,
        min_size=(1000, 650),
    )

    # Best-effort in-window title-bar icon — supported on some pywebview
    # versions/backends, not others. This is NOT what controls the
    # Windows taskbar/Explorer icon once packaged — that comes from the
    # --icon flag baked into the .exe at build time (see build.py). If
    # this argument isn't supported by your installed pywebview version,
    # it's simply ignored rather than crashing the app.
    try:
        window = webview.create_window(APP_TITLE, INDEX_HTML, icon=ICON_PATH, **window_kwargs)
    except TypeError:
        window = webview.create_window(APP_TITLE, INDEX_HTML, **window_kwargs)

    # File-picker dialogs (used by the Import Data screen) need a real
    # window reference — the Api object doesn't have one until the
    # window actually exists, so it's attached here rather than in
    # Api.__init__.
    api.window = window

    webview.start()


if __name__ == "__main__":
    main()
