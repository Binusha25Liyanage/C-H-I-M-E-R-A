"""
startup.py
What the splash screen shows while Chimera opens. Every part of the loading bar
is a real step:

  start       opening Chimera's database and settings
  sfa         logging in to SFA once (in the background, no window) to check the
              connection works
  itinerary   reading the working territories' itineraries from SFA — the same
              read Outlet Updates does automatically (itinerary_automation)

The first open of the day runs all three; later opens the same day only check
SFA, plus the itineraries if some are still due. Nothing in SFA is changed —
every step only reads. The splash can be skipped at any time: what's left keeps
running in the background (Running now shows it).
"""

import threading
import time
from datetime import date, datetime

import automation_store as AS

STEPS = [("start", "Starting Chimera"), ("sfa", "Checking SFA connection"), ("itinerary", "Itinerary updates")]
WEIGHT = {"start": 10, "sfa": 30, "itinerary": 60}

_LOCK = threading.Lock()
_STATE = {}


def _reset():
    _STATE.clear()
    _STATE.update(active=True, done=False, started_at=datetime.now().isoformat(timespec="seconds"),
                  full=False, sfa="checking", message="Starting Chimera…", percent=0,
                  steps=[{"id": i, "label": l, "status": "pending", "message": "", "progress": 0} for i, l in STEPS])


def _step(sid, **kw):
    with _LOCK:
        for s in _STATE["steps"]:
            if s["id"] == sid:
                s.update(kw)
        if kw.get("message") and kw.get("status") == "running":
            _STATE["message"] = kw["message"]
        _STATE["percent"] = round(sum(WEIGHT[s["id"]] * (1 if s["status"] in ("done", "skipped", "failed")
                                                         else s.get("progress", 0)) for s in _STATE["steps"]))


def snapshot():
    with _LOCK:
        if not _STATE:
            return {"active": False, "done": False, "percent": 0, "steps": [], "message": "", "sfa": "unknown"}
        return {k: (list(map(dict, v)) if k == "steps" else v) for k, v in _STATE.items()}


def is_first_open_today(today=None):
    today = today or date.today().isoformat()
    return AS.get_automation_settings().get("startup_full_day") != today


def check_sfa(timeout_ms=30000):
    """Logs in to SFA once without showing a window. -> (ok, message)"""
    import sfa_config
    import sfa_connection as C
    cfg = sfa_config.load_config()
    if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
        return False, "SFA login isn't set up — Settings › SFA Connection."
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel=sfa_config.SUPPORTED_BROWSERS.get(cfg.get("browser"), "chrome"))
            except Exception:
                browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_default_timeout(timeout_ms)
                user_ok, pass_ok, form = C._login(page, cfg)
                if not (user_ok and pass_ok):
                    return False, "SFA's login page didn't load" if not form else "SFA's login form couldn't be filled"
                ok, welcome = C._verify_logged_in(page)
                if not ok:
                    return False, "SFA refused the login — check the username and password (Settings › SFA Connection)"
                return True, f"Connected{(' — ' + welcome) if welcome else ''}"
            finally:
                browser.close()
    except Exception as e:
        t = str(e)
        if "ERR_NAME_NOT_RESOLVED" in t or "ERR_INTERNET_DISCONNECTED" in t or "ERR_CONNECTION" in t:
            return False, "No internet connection, or SFA can't be reached"
        if "Timeout" in t:
            return False, "SFA didn't answer in time"
        return False, f"Couldn't reach SFA ({type(e).__name__})"


def _run_itinerary(full):
    import itinerary_automation as IA
    import outlet_store as OS
    st = IA.settings()
    if not st["auto"]:
        _step("itinerary", status="skipped", message="Automatic itinerary updates are off (Outlet Updates › Settings)")
        return
    if st["paused"]:
        _step("itinerary", status="skipped", message="Automation is paused")
        return
    if not OS.list_territories(enabled_only=True):
        _step("itinerary", status="skipped", message="No working territories in Outlet Updates yet")
        return
    day, due = OS.due_territories(None, st["prepare_time"])
    if not due and IA.LIVE.snapshot().get("active") is not True:
        _step("itinerary", status="done", progress=1, message=f"Itineraries for {day:%d %b} are up to date")
        return
    _step("itinerary", status="running", message=f"Reading itineraries… 0 of {len(due)} territories")
    box = {}
    if not IA.LIVE.snapshot().get("active"):        # already reading (the scheduler)? just follow it
        def go():
            try:
                box["res"] = IA.run_read(due, [(day.year, day.month)], trigger="startup")
            except Exception as e:
                box["res"] = {"ok": False, "message": str(e)}
        t = threading.Thread(target=go, daemon=True, name="chimera-startup-itinerary")
        t.start()
        time.sleep(1.5)
    while True:
        s = IA.LIVE.snapshot()
        lanes = s.get("lanes") or []
        n = len(lanes) or len(due)
        fin = sum(1 for l in lanes if l.get("status") in ("done", "missing", "failed", "cancelled", "skipped"))
        if s.get("active"):
            reading = next((l["name"] for l in lanes if l.get("status") == "running"), "")
            _step("itinerary", status="running", progress=(fin / n) if n else 0,
                  message=f"Reading itineraries… {fin} of {n} territories" + (f" · {reading}" if reading else ""))
            time.sleep(0.7)
            continue
        if "res" not in box and not s.get("result") and time.time() < box.setdefault("until", time.time() + 5):
            time.sleep(0.5)          # the run hasn't started yet
            continue
        res = box.get("res") or s.get("result") or {}
        if res.get("status") == "busy":
            _step("itinerary", status="skipped", message="Another SFA run is busy — itineraries will be read after it")
        else:
            _step("itinerary", status="done" if res.get("ok", True) and res.get("status") != "failed" else "failed",
                  progress=1, message=res.get("message") or "Itineraries read")
        return


def run(force_full=False):
    _reset()
    try:
        _step("start", status="running", message="Opening Chimera's data…")
        full = force_full or is_first_open_today()
        _STATE["full"] = full
        _step("start", status="done", progress=1, message="Chimera's data is ready")

        _step("sfa", status="running", progress=0.2, message="Checking SFA connection (logging in)…")
        ok, msg = check_sfa()
        _STATE["sfa"] = "connected" if ok else "offline"
        _step("sfa", status="done" if ok else "failed", progress=1, message=msg)
        if not ok:
            _step("itinerary", status="skipped", message="Skipped — SFA isn't reachable")
            _STATE["message"] = f"SFA: {msg}. Opening the dashboard anyway."
            return
        _run_itinerary(full)
        if full:
            AS.save_automation_settings({"startup_full_day": date.today().isoformat()})
        _STATE["message"] = "Ready"
    except Exception as e:
        _STATE["message"] = f"Startup check stopped: {e}"
    finally:
        with _LOCK:
            for s in _STATE.get("steps", []):
                if s["status"] in ("pending", "running"):
                    s["status"] = "skipped"
            _STATE["percent"] = 100
            _STATE["active"] = False
            _STATE["done"] = True


def start(force_full=False):
    """Starts the startup steps once per Chimera window (a page reload just follows the same run)."""
    if _STATE.get("active") or _STATE.get("done"):
        return snapshot()
    _reset()
    threading.Thread(target=run, args=(force_full,), daemon=True, name="chimera-startup").start()
    return snapshot()
