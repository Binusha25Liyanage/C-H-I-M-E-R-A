"""
app.py
Entry point for the desktop app. Run this with:

    python app.py

This opens one native window (no browser tab, no server URL to visit) with
the frontend/index.html page rendered inside it, and wires up the Api
bridge from api.py so JavaScript in that page can call real Python code.
"""

import logging
import os
import sys
import webview

from api import Api

APP_TITLE = "C H I M E R A"
APP_ICON = "frontend/assets/chimera.ico"


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


def api_public_non_methods(obj):
    """Public attributes that aren't methods. Must be empty, or pywebview can't expose the API."""
    import inspect
    out = []
    for name in dir(obj):
        if name.startswith("_"):
            continue
        attr = getattr(obj, name)
        if not (inspect.ismethod(attr) or inspect.isfunction(attr)):
            out.append(name)
    return out


TITLE_BAR_RGB = (0x1C, 0x1C, 0x1E)   # same charcoal as the sidebar (theme --color-sidebar-bg)


def style_title_bar(window, rgb=TITLE_BAR_RGB):
    """
    Colours the native Windows title bar to match the app.
    Windows 11: exact caption colour + white title text (DWM attributes 35/36).
    Windows 10 (2004+): no custom colours, but the dark title bar (attribute 20,
    or 19 on older builds) is close to the sidebar colour.
    Anything else (or any failure) leaves the normal title bar — never crashes.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = None
        try:
            hwnd = int(window.native.Handle.ToInt64())
        except Exception:
            hwnd = ctypes.windll.user32.FindWindowW(None, APP_TITLE)
        if not hwnd:
            logging.info("Title bar: window handle not found")
            return False
        dwm = ctypes.windll.dwmapi

        def set_attr(attr, value):
            v = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), ctypes.c_uint(attr), ctypes.byref(v), ctypes.sizeof(v))

        if set_attr(20, 1) != 0:          # dark title bar (Windows 10 2004+ / 11)
            set_attr(19, 1)               # same, older Windows 10 builds
        r, g, b = rgb
        caption = set_attr(35, r | (g << 8) | (b << 16))   # COLORREF is 0x00BBGGRR (Windows 11)
        set_attr(36, 0x00FFFFFF)                            # title text: white
        set_attr(34, r | (g << 8) | (b << 16))              # window border
        logging.info("Title bar styled (caption colour %s)", "applied" if caption == 0 else "not supported, dark mode only")
        return True
    except Exception as e:
        logging.info("Title bar styling skipped: %s", e)
        return False


def main():
    # Windows Task Scheduler launches "Chimera.exe --scheduled": run the due
    # SFA automation headless and exit, without opening a window.
    if "--scheduled" in sys.argv or "--run-now" in sys.argv:
        import run_scheduled
        sys.exit(run_scheduled.main(["--now"] if "--run-now" in sys.argv else []))

    # Keep a log so problems (including pywebview's own errors) leave a trace.
    log_dir = os.path.join(os.path.expanduser("~"), "Chimera", "logs")
    os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(filename=os.path.join(log_dir, "chimera.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.info("Chimera starting")

    api = Api()
    bad = api_public_non_methods(api)
    if bad:  # would break pywebview's API discovery — see Api.__init__
        logging.error("Api has public non-method attributes: %s", bad)

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
    api._win = window

    # Scheduled SFA runs while the window is open (Task Scheduler covers when it's closed).
    import scheduler
    api._scheduler = scheduler.InAppScheduler()
    api._scheduler.start()

    window.events.shown += lambda: style_title_bar(window)

    webview.start()


if __name__ == "__main__":
    main()
