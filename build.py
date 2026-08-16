"""
build.py
Packages Chimera into a single standalone .exe using PyInstaller.

Run this ON Windows to get a Windows .exe (PyInstaller does not
cross-compile — building on Mac/Linux produces a Mac/Linux program only).

Usage:
    python build.py            # onefile — one .exe, slower to start
    python build.py --onedir   # a folder — starts faster, share the folder

The --icon flag below is what actually sets the icon Windows shows in
the taskbar, Explorer, and Alt-Tab — the runtime `icon=` argument in
app.py only affects the in-window title bar icon (and only on some
pywebview versions), so this is the step that matters for the icon you
actually see everywhere else.
"""

import os
import shutil
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(APP_DIR, "backend")
FRONTEND_DIR = os.path.join(APP_DIR, "frontend")
DIST_DIR = os.path.join(APP_DIR, "dist")
BUILD_DIR = os.path.join(APP_DIR, "build")
APP_NAME = "Chimera"
ICON_PATH = os.path.join(FRONTEND_DIR, "assets", "chimera.ico")
DATA_SEP = ";" if os.name == "nt" else ":"


def _ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller isn't installed — installing it now...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def main():
    onedir = "--onedir" in sys.argv
    _ensure_pyinstaller()

    for d in (DIST_DIR, BUILD_DIR):
        if os.path.exists(d):
            shutil.rmtree(d)
    spec_file = os.path.join(APP_DIR, f"{APP_NAME}.spec")
    if os.path.exists(spec_file):
        os.remove(spec_file)

    if not os.path.exists(ICON_PATH):
        print(f"WARNING: {ICON_PATH} not found — the exe will use the default "
              f"PyInstaller icon instead of the Chimera logo.")

    args = [
        os.path.join(BACKEND_DIR, "app.py"),
        "--name", APP_NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
        # Bundle the whole frontend folder (html/css/js/assets) into the
        # exe under a "frontend" folder, matching the relative path
        # app.py expects (../frontend from backend/).
        "--add-data", f"{FRONTEND_DIR}{DATA_SEP}frontend",
    ]
    args.append("--onedir" if onedir else "--onefile")

    if os.path.exists(ICON_PATH):
        args += ["--icon", ICON_PATH]

    print("Running PyInstaller:")
    print("  " + " ".join(args))
    print()

    import PyInstaller.__main__
    PyInstaller.__main__.run(args)

    exe_name = f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME
    if onedir:
        exe_path = os.path.join(DIST_DIR, APP_NAME, exe_name)
        share_note = f"Share the whole folder:\n  {os.path.join(DIST_DIR, APP_NAME)}"
    else:
        exe_path = os.path.join(DIST_DIR, exe_name)
        share_note = f"Share this one file:\n  {exe_path}"

    print()
    print("=" * 64)
    if os.path.exists(exe_path):
        print("Build complete.")
        print(share_note)
        if os.name != "nt":
            print()
            print("NOTE: built on a non-Windows machine — this is NOT a Windows")
            print(".exe. Run this script on Windows to get one with the icon set.")
    else:
        print("Build finished, but the expected output wasn't found at:")
        print(f"  {exe_path}")
        print(f"Check {DIST_DIR} for what PyInstaller actually produced.")
    print("=" * 64)


if __name__ == "__main__":
    main()
