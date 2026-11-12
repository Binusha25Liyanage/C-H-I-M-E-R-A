"""
desktop_notify.py
Windows notifications (the pop-ups that also stay in the notification
centre) for what Chimera does: a scheduled time arriving, a run starting,
something waiting for your approval, a run finishing, a run failing.
Shown whether Chimera's window is open, minimised, or closed (scheduled
runs then work in their own process and notify from there).

How it works — no extra packages:
  * Chimera is registered once for the current Windows user under its app
    identity (the same one its taskbar icon uses), with its name and logo,
    so notifications are grouped under "Chimera" like WhatsApp's.
  * A "chimera:" link is registered too: clicking a notification opens
    Chimera on the right screen (it's brought to the front if it is open).
  * Each notification is shown by Windows PowerShell's built-in toast API,
    in the background, so a slow or failed notification never holds up a run.

Settings (automation_store): notify_desktop (master switch), notify_started,
notify_approval, notify_finished, notify_failed.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from xml.sax.saxutils import escape

import automation_store as AS

APP_ID = "Binusha.Chimera.SFA"          # = app.py's APP_ID (taskbar identity)
APP_NAME = "Chimera"
KINDS = ("started", "approval", "finished", "failed")
SOURCES = {"reports": "Daily reports", "loadings": "Distributor loadings", "itinerary": "Itineraries",
           "outlets": "Outlet updates", "distributors": "Distributor list", "whatsapp": "WhatsApp",
           "schedule": "Schedules", "chimera": "Chimera"}
ICON_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "assets", "chimera-logo-round.png")
DATA_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "data")
REQUEST_FILE = os.path.join(DATA_DIR, "open_request.json")

_recent = {}                 # tag -> time shown (no duplicates within a few seconds)
_lock = threading.Lock()
_STATE = {"setup": False, "last_error": "", "last_shown": None, "history": []}


# ---------------------------------------------------------------------------
# One-time registration (current user only, no admin rights)
# ---------------------------------------------------------------------------
def _icon_path():
    """A stable copy of the logo (a packaged Chimera unpacks to a new temp folder on every start)."""
    dst = os.path.join(DATA_DIR, "chimera-notify.png")
    try:
        src = ICON_SRC
        if getattr(sys, "frozen", False):
            src = os.path.join(getattr(sys, "_MEIPASS", ""), "frontend", "assets", "chimera-logo-round.png")
        if os.path.exists(src) and (not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src)):
            os.makedirs(DATA_DIR, exist_ok=True)
            shutil.copyfile(src, dst)
    except OSError:
        pass
    return dst if os.path.exists(dst) else ""


def launch_command():
    """What Windows runs when a notification is clicked ("%1" = the chimera: link)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --open "%1"'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    app = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    return f'"{pyw if os.path.exists(pyw) else exe}" "{app}" --open "%1"'


def setup():
    """Registers Chimera's name + logo for notifications and the chimera: link. Safe to call every start."""
    if os.name != "nt":
        return False
    try:
        import winreg
        icon = _icon_path()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            if icon:
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, icon)
            winreg.SetValueEx(k, "IconBackgroundColor", 0, winreg.REG_SZ, "FFB5121B")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\chimera") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Chimera")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\chimera\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, launch_command())
        _STATE["setup"] = True
        return True
    except Exception as e:
        _STATE["last_error"] = f"Couldn't register Chimera for notifications: {e}"
        return False


# ---------------------------------------------------------------------------
# Showing a notification
# ---------------------------------------------------------------------------
_PS = r"""
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml(@'
__XML__
'@)
$t = New-Object Windows.UI.Notifications.ToastNotification $x
$t.Tag = '__TAG__'
$t.Group = 'chimera'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('__APPID__').Show($t)
"""


def toast_xml(title, body, action="", kind="finished"):
    link = escape(f"chimera:open?action={action or 'overview'}", {'"': "&quot;"})
    btn = {"approval": "Review now", "failed": "See what happened"}.get(kind, "Open Chimera")
    return (f'<toast activationType="protocol" launch="{link}" duration="{"long" if kind in ("approval", "failed") else "short"}">'
            f'<visual><binding template="ToastGeneric"><text>{escape(title[:120])}</text><text>{escape(body[:400])}</text>'
            f'<text placement="attribution">Chimera · {datetime.now():%H:%M}</text></binding></visual>'
            f'<actions><action content="{btn}" activationType="protocol" arguments="{link}"/></actions>'
            f'<audio src="ms-winsoundevent:Notification.{"Reminder" if kind in ("approval", "failed") else "Default"}"/></toast>')


def _show_now(title, body, action, kind, tag):
    if os.name != "nt":
        return True, "Not on Windows — notification not shown."
    if not _STATE["setup"]:
        setup()
    script = _PS.replace("__XML__", toast_xml(title, body, action, kind)).replace("__TAG__", tag.replace("'", "")[:60]) \
        .replace("__APPID__", APP_ID)
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-WindowStyle", "Hidden", "-EncodedCommand", enc], capture_output=True, text=True,
                           timeout=40, creationflags=0x08000000)       # CREATE_NO_WINDOW
    except Exception as e:
        _STATE["last_error"] = f"{type(e).__name__}: {e}"
        return False, _STATE["last_error"]
    if r.returncode != 0:
        _STATE["last_error"] = (r.stderr or r.stdout or "PowerShell error").strip().splitlines()[0][:300]
        return False, _STATE["last_error"]
    _STATE["last_error"] = ""
    _STATE["last_shown"] = datetime.now().isoformat(timespec="seconds")
    return True, ""


def enabled(kind):
    s = AS.get_automation_settings()
    return s.get("notify_desktop", "1") == "1" and s.get(f"notify_{kind}", "1") == "1"


def event(kind, source, title, body="", action="", tag=None):
    """kind: started | approval | finished | failed. Returns at once; shows in the background."""
    try:
        if kind not in KINDS or not enabled(kind):
            return False
    except Exception:
        return False
    tag = tag or f"{source}-{kind}-{title}"[:60]
    now = time.time()
    with _lock:
        if now - _recent.get(tag, 0) < 8:          # the same notification twice in a row
            return False
        _recent[tag] = now
        _STATE["history"] = ([{"t": datetime.now().strftime("%H:%M:%S"), "kind": kind, "source": source,
                               "title": title, "body": body}] + _STATE["history"])[:30]
    threading.Thread(target=_show_now, args=(title, body, action, kind, tag), daemon=True,
                     name="chimera-notify").start()
    return True


def test():
    """Settings › Send a test notification — shown now; returns (ok, message)."""
    if os.name != "nt":
        return False, "Windows notifications only work on Windows."
    setup()
    ok, err = _show_now("Chimera notifications are on",
                        "This is how Chimera tells you a scheduled time has arrived, something needs your approval, "
                        "or a run has finished or failed.", "settings", "finished", "test")
    return ok, ("Sent — look at the bottom-right corner of the screen." if ok else
                f"Windows didn't show it: {err}. Check Settings › System › Notifications (Chimera must be on, and "
                "Do not disturb off).")


def status():
    return {"windows": os.name == "nt", "registered": _STATE["setup"], "last_error": _STATE["last_error"],
            "last_shown": _STATE["last_shown"], "history": _STATE["history"][:10]}


# ---------------------------------------------------------------------------
# Clicking a notification -> "chimera:open?action=…" -> Chimera opens that screen
# ---------------------------------------------------------------------------
def action_from_link(link):
    link = (link or "").strip().strip('"')
    if "action=" in link:
        return link.split("action=", 1)[1].split("&")[0][:60]
    return "overview"


def request_open(link):
    """Chimera was started by a notification click: remember which screen to open."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(REQUEST_FILE, "w", encoding="utf-8") as f:
            json.dump({"action": action_from_link(link), "at": time.time()}, f)
    except OSError:
        pass


def take_open_request():
    """The running window asks (every few seconds) whether a notification was clicked."""
    try:
        with open(REQUEST_FILE, encoding="utf-8") as f:
            r = json.load(f)
        os.remove(REQUEST_FILE)
        return r["action"] if time.time() - r.get("at", 0) < 300 else None
    except (OSError, ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Words for each run (used by sfa_automation.LiveState)
# ---------------------------------------------------------------------------
RUN_WORDS = {
    # source: (what it is, screen to open)
    "reports": ("SFA reports", "automation"),
    "loadings:prepare": ("Reading loadings for approval", "loadings"),
    "loadings:confirm": ("Confirming loadings in SFA", "loadings"),
    "loadings:sync": ("Updating the distributor list", "loadings"),
    "loadings:add": ("Adding a loading", "loadings"),
    "itinerary": ("Reading itineraries", "outlet-updates"),
    "itinerary:detect": ("Finding territories", "outlet-updates"),
    "outlets:sequence": ("Rep sheets — SFA", "rep-sheets"),
    "outlets:routes": ("Reading SFA's route list", "rep-sheets"),
}
TRIGGER_WORDS = {"scheduled": "scheduled", "schedule": "scheduled", "auto": "automatic", "manual": "", "cli": "",
                 "upload": "", "file": ""}


def run_words(source, kind):
    key = f"{source}:{kind}" if kind else source
    what, view = RUN_WORDS.get(key) or RUN_WORDS.get(source) or (SOURCES.get(source, source), "overview")
    return what, view


def run_started(source, snap):
    what, view = run_words(source, snap.get("kind"))
    trig = str(snap.get("trigger") or "")
    if trig.startswith("scheduled"):
        return                     # the scheduler already said "<job> — <time> is starting now"
    sched = trig.startswith(("schedule", "auto"))
    slot = snap.get("slot") or ""
    title = f"{what} — {'scheduled ' + slot if sched and slot else 'scheduled run' if sched else 'started'}"
    lanes = snap.get("lanes") or []
    body = (f"{len(lanes)} item(s): " + ", ".join(l.get("name", "") for l in lanes[:4]) + (" …" if len(lanes) > 4 else "")) \
        if lanes else "Chimera is working on it in the background."
    if snap.get("dry_run"):
        body = "DRY RUN — nothing will be saved in SFA. " + body
    event("started", source, title, body, action=view, tag=f"{source}-start-{snap.get('started_at', '')}")


def _approval(source, snap, result):
    """Marks the result when this run left something waiting for the user's approval."""
    try:
        since = snap.get("started_at") or ""
        rr = result.get("report_run") or {}
        if source == "reports" and rr.get("status") == "pending_review":
            result.update(needs_approval=True, approval_action=f"preview:{rr.get('run_id')}",
                          message=f"Reports for {snap.get('report_date') or rr.get('report_date', '')} are ready — "
                                  "approve them to save and send to WhatsApp.")
        elif source == "loadings" and snap.get("kind") == "prepare":
            import loading_store
            pend = [p for p in loading_store.pending_items() if (p.get("prepared_at") or "") >= since]
            if pend:
                result.update(needs_approval=True, approval_action="loadings-approve",
                              message=f"{len(pend)} loading(s) ready: " + ", ".join(p["distributor_name"] for p in pend[:4])
                                      + (" …" if len(pend) > 4 else "") + " — approve to confirm them in SFA.")
        elif source == "outlets" and snap.get("kind") == "sequence":
            import sequence_store
            ready = [x for x in sequence_store.list_sheets(("ready",), limit=50) if (x.get("read_at") or "") >= since
                     and not x.get("dry_run")]
            if ready:
                result.update(needs_approval=True, approval_action="rep-sheets",
                              message=f"{len(ready)} rep sheet(s) ready to review: " +
                                      ", ".join(x["route_name"] for x in ready[:4]) + (" …" if len(ready) > 4 else ""))
    except Exception:
        pass


def run_finished(source, snap, result):
    result = dict(result)
    what, view = run_words(source, snap.get("kind"))
    status = str(result.get("status") or "")
    msg = str(result.get("message") or "")
    if status in ("nothing",) or (status == "busy" and not str(snap.get("trigger") or "").startswith(("sched", "auto"))):
        return
    _approval(source, snap, result)
    msg = str(result.get("message") or "")
    if result.get("needs_approval"):
        kind, title = "approval", f"{what} — waiting for your approval"
        view = result.get("approval_action") or view
    elif status in ("failed", "busy", "partial", "interrupted") or (result.get("ok") is False and status != "cancelled"):
        kind, title = "failed", f"{what} — {'could not start' if status == 'busy' else 'problem'}"
    elif status == "cancelled":
        kind, title = "finished", f"{what} — stopped"
    else:
        kind, title = "finished", f"{what} — done"
    event(kind, source, title, msg or "Finished.", action=view, tag=f"{source}-end-{snap.get('started_at', '')}")
