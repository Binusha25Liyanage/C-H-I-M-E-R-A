"""
desktop_icon.py — makes the Chimera desktop shortcut glitch now and then.

Windows can't animate a desktop icon, so Chimera does it by hand: every ~30 seconds it switches the Chimera
shortcut's icon through a few glitch frames to the white-on-dark logo, holds it a moment, and glitches back.
Each switch changes the shortcut's IconLocation and tells Explorer to redraw it.

Settings › Customize Appearance › Desktop icon:
  look_icon        open   — glitch while Chimera is open (default)
                   always — also when Chimera is closed: a tiny helper starts with Windows
                   off    — the icon stays still (and the helper is removed)
  look_icon_every  seconds between glitches (10–600, default 30; each wait varies a little)

Only shortcuts on *your* Desktop that open this Chimera (target inside the Chimera folder / Chimera.exe, or
already using a Chimera icon) are touched, and they always end on the normal icon — also when Chimera closes.
Does nothing on anything other than Windows.
"""
import logging
import os
import random
import shutil
import subprocess
import sys
import threading
import time

log = logging.getLogger("desktop_icon")
IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ChimeraDesktopIcon"
ALIVE_MUTEX = "Local\\ChimeraDesktopIconHelper"       # held for life by the always-on helper
GLITCH_MUTEX = "Local\\ChimeraDesktopIconGlitch"      # held while a glitch is playing, so two never overlap

# Three looks — normal -> neon red -> white-on-dark -> normal — with a glitch between each, like the sidebar logo
# (slices jump, cyan/magenta misprint). Each glitch frame stays ~180 ms: Explorer redraws desktop icons slowly,
# and shorter frames were simply skipped (it looked like a plain swap). (frame file, how long it stays, in ms)
GLITCH_MS = 180
LOOKS = ("normal", "neon", "dark")
SEQUENCE = []
for _a, _b, _hold in (("normal", "neon", 1500), ("neon", "dark", 1500), ("dark", "normal", 0)):
    SEQUENCE += [(f"{_a}_to_{_b}_{i}.ico", GLITCH_MS) for i in (1, 2, 3)] + [(f"{_b}.ico", _hold)]
FRAME_FILES = sorted({f for f, _ in SEQUENCE})


def _app_root():
    """The folder a Chimera shortcut points into (the project folder, or the folder of Chimera.exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _assets_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "frontend", "assets", "icon_glitch")
    return os.path.join(_app_root(), "frontend", "assets", "icon_glitch")


def frames_dir():
    """A stable copy of the frames (a packaged Chimera unpacks to a temp folder that disappears)."""
    import db
    d = os.path.join(db.APP_DATA_DIR, "icon_glitch")
    os.makedirs(d, exist_ok=True)
    src = _assets_dir()
    for f in FRAME_FILES:
        a, b = os.path.join(src, f), os.path.join(d, f)
        try:
            if os.path.isfile(a) and (not os.path.isfile(b) or os.path.getsize(a) != os.path.getsize(b)):
                shutil.copyfile(a, b)
        except OSError as e:
            log.info("Couldn't copy %s: %s", f, e)
    return d


def settings():
    import automation_store as AS
    s = AS.get_automation_settings()
    mode = s.get("look_icon", "open")
    try:
        every = max(10, min(600, int(s.get("look_icon_every", "30"))))
    except ValueError:
        every = 30
    return mode, every


# ---------------------------------------------------------------- Windows plumbing

def _mutex_exists(name):
    if not IS_WIN:
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenMutexW(0x00100000, False, name)          # SYNCHRONIZE
    if h:
        k32.CloseHandle(h)
        return True
    return False


def _take_mutex(name):
    """-> handle if we now own it, None if someone else does."""
    if not IS_WIN:
        return None
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.CreateMutexW(None, True, name)
    if not h:
        return None
    if k32.GetLastError() == 183:                         # ERROR_ALREADY_EXISTS
        k32.CloseHandle(h)
        return None
    return h


def _release_mutex(h):
    if IS_WIN and h:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.ReleaseMutex(h)
        k32.CloseHandle(h)


def _refresh(path):
    """Tell Explorer this shortcut changed so it redraws the icon now."""
    if IS_WIN:
        import ctypes
        try:
            # UPDATEITEM, PATHW | FLUSH: Explorer handles it before we go on, so every frame gets drawn
            ctypes.windll.shell32.SHChangeNotify(0x00002000, 0x0005 | 0x1000, ctypes.c_wchar_p(path), None)
        except Exception:
            pass


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


class _Shell:
    """One hidden PowerShell kept open, so each icon switch takes milliseconds instead of starting PowerShell."""

    def __init__(self):
        flags = 0x08000000 if IS_WIN else 0                # CREATE_NO_WINDOW
        self.p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-Command", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=flags)
        self.run("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "
                 "$sh = New-Object -ComObject WScript.Shell")

    def run(self, cmd, timeout=10):
        mark = f"__done_{random.randint(1, 10**9)}__"
        self.p.stdin.write(cmd + f"\nWrite-Output '{mark}'\n")
        self.p.stdin.flush()
        out, t0 = [], time.time()
        while time.time() - t0 < timeout:
            line = self.p.stdout.readline()
            if not line:
                break
            line = line.rstrip("\r\n")
            if line == mark:
                return out
            out.append(line)
        raise RuntimeError("PowerShell didn't answer")

    def close(self):
        try:
            self.p.stdin.write("exit\n")
            self.p.stdin.flush()
            self.p.wait(3)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass


def find_shortcuts(sh, root=None):
    """Chimera shortcuts on your Desktop: they open something inside the Chimera folder, or already use a Chimera icon."""
    root = (root or _app_root()).rstrip("\\/")
    lines = sh.run(
        "$d = [Environment]::GetFolderPath('Desktop'); "
        "Get-ChildItem -LiteralPath $d -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object { "
        "$l = $sh.CreateShortcut($_.FullName); "
        "Write-Output ($_.FullName + '|' + $l.TargetPath + '|' + $l.IconLocation) }")
    return [p for p, t, i in (ln.split("|", 2) for ln in lines if ln.count("|") >= 2) if is_chimera_shortcut(t, i, root)]


def is_chimera_shortcut(target, icon, root):
    t, i, r = (target or "").lower().replace("/", "\\"), (icon or "").lower(), root.lower().replace("/", "\\")
    return bool(r) and (t.startswith(r + "\\") or t == r) \
        or "chimera.ico" in i or "icon_glitch" in i or t.rsplit("\\", 1)[-1] == "chimera.exe"


def _set_icon(sh, lnk, ico):
    sh.run(f"$l = $sh.CreateShortcut({_ps_quote(lnk)}); $l.IconLocation = {_ps_quote(ico + ',0')}; $l.Save()")
    _refresh(lnk)


def glitch_once(sh, links, folder):
    """Play one glitch on every Chimera shortcut. Always ends on the normal icon."""
    h = _take_mutex(GLITCH_MUTEX)
    if IS_WIN and not h:
        return False                                       # another Chimera process is glitching right now
    try:
        for f, ms in SEQUENCE:
            for lnk in links:
                _set_icon(sh, lnk, os.path.join(folder, f))
            if ms:
                time.sleep(ms / 1000)
        return True
    finally:
        for lnk in links:                                  # whatever happened, put the normal icon back
            try:
                _set_icon(sh, lnk, os.path.join(folder, "normal.ico"))
            except Exception:
                pass
        _release_mutex(h)


# ---------------------------------------------------------------- start with Windows (mode "always")

def helper_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --icon-helper'
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{w if os.path.isfile(w) else exe}" "{os.path.abspath(__file__)}" --helper'


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, helper_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        log.info("Couldn't change start-with-Windows: %s", e)


def start_helper():
    """Start the always-on helper now (it carries on after Chimera closes) unless it's already running."""
    if not IS_WIN or _mutex_exists(ALIVE_MUTEX):
        return
    cmd = helper_command()
    try:
        subprocess.Popen(cmd, creationflags=0x00000008 | 0x00000200 | 0x08000000, close_fds=True)  # detached, own group, no window
    except Exception as e:
        log.info("Couldn't start the desktop icon helper: %s", e)


def apply_mode():
    """After the setting changes (and at start): start-with-Windows on/off, helper running or not."""
    mode, _ = settings()
    set_autostart(mode == "always")
    if mode == "always":
        start_helper()


# ---------------------------------------------------------------- loops

def _loop(stop, is_helper):
    sh = links = folder = None
    next_look = 0
    try:
        while not stop.is_set():
            mode, every = settings()
            if is_helper and mode != "always":
                return                                     # switched to "open"/"off": the helper bows out
            if not is_helper and (mode == "off" or (mode == "always" and _mutex_exists(ALIVE_MUTEX))):
                stop.wait(5)                               # off, or the helper is doing it
                continue
            if sh is None:
                sh, folder = _Shell(), frames_dir()
            if time.time() >= next_look:                   # look for the shortcut again now and then (it may be new)
                links, next_look = find_shortcuts(sh), time.time() + 300
            if links:
                glitch_once(sh, links, folder)
            stop.wait(every * random.uniform(0.8, 1.2))
    except Exception as e:
        log.info("Desktop icon glitch stopped: %s", e)
    finally:
        if sh:
            try:
                for lnk in links or []:
                    _set_icon(sh, lnk, os.path.join(folder, "normal.ico"))
            except Exception:
                pass
            sh.close()


_STOP = threading.Event()
_THREAD = {"t": None}


def start_in_app():
    """Called when Chimera opens: glitch while it's open (mode "open"), or make sure the helper runs ("always")."""
    if not IS_WIN:
        return
    try:
        apply_mode()
    except Exception as e:
        log.info("Desktop icon setting not applied: %s", e)
    t = threading.Thread(target=_loop, args=(_STOP, False), daemon=True, name="desktop-icon")
    _THREAD["t"] = t
    t.start()


def stop_in_app():
    """Chimera is closing: finish the glitch in progress so the shortcut is left on the normal icon."""
    _STOP.set()
    t = _THREAD.get("t")
    if t and t.is_alive():
        t.join(4)


def helper_main():
    """The always-on helper (starts with Windows): runs until the setting is no longer "always"."""
    if not IS_WIN:
        return 0
    h = _take_mutex(ALIVE_MUTEX)
    if not h:
        return 0                                           # already running
    try:
        _loop(threading.Event(), True)
    finally:
        _release_mutex(h)
    return 0


if __name__ == "__main__" and "--helper" in sys.argv:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(helper_main())


def try_now():
    """One glitch right now (Settings › Try it now). -> {ok, message}"""
    if not IS_WIN:
        return {"ok": False, "message": "The desktop icon glitch works on Windows only."}
    sh = None
    try:
        sh, folder = _Shell(), frames_dir()
        links = find_shortcuts(sh)
        if not links:
            return {"ok": False, "message": "No Chimera shortcut found on your Desktop — make one with "
                                             "'Create Desktop Shortcut.vbs' in the Chimera folder."}
        if not glitch_once(sh, links, folder):
            return {"ok": False, "message": "It's glitching right now — try again in a moment."}
        return {"ok": True, "message": "Look at your Desktop — it glitched." if len(links) == 1
                else f"Glitched {len(links)} Chimera shortcuts on your Desktop."}
    except Exception as e:
        return {"ok": False, "message": f"Couldn't change the shortcut's icon: {e}"}
    finally:
        if sh:
            sh.close()
