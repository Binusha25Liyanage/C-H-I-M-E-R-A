"""
rep_attendance_automation.py
Rep Attendance runs:

  run()      SFA › REP ATTENDANCE REPORT (/it/repattendancereport2): Date From = Date To = the day,
             SEARCH, the Excel icon -> read -> save the day -> build the images (classic /
             professional / both) -> send to the WhatsApp chats set on the Rep Attendance screen,
             or wait for approval (a scheduled time marked "Ask me first"). Read only in SFA.
  rebuild()  the same images from what Chimera already has for a day (after an upload or your
             corrections) — no SFA.
  send()     sends a run's images to the chats (also used to approve a waiting run).
"""

import asyncio
import json
import os
import threading
import time
from datetime import date, datetime

import automation_store as AS
import rep_attendance as R
import rep_attendance_images as RI
import sfa_automation as SA
import sfa_config

STEPS = ["login", "open", "filters", "search", "export", "read", "build", "send"]
STEP_LABELS = {"login": "Log in", "open": "Open report", "filters": "Dates", "search": "Search", "export": "Excel",
               "read": "Read", "build": "Images", "send": "WhatsApp"}
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "downloads", "rep_attendance")
LIVE = SA.LiveState("attendance")
_ACTIVE = {"loop": None, "browser": None, "thread": None}


class StepError(Exception):
    def __init__(self, step, message):
        super().__init__(message)
        self.step, self.message = step, message


def settings():
    s = AS.get_automation_settings()
    try:
        chats = json.loads(s.get("att_chats") or "[]")
    except ValueError:
        chats = []
    return {"url": s.get("att_url") or R.DEFAULT_URL, "style": s.get("att_style") or "both",
            "send_style": s.get("att_send_style") or "classic", "chats": chats, "send_pdf": s.get("att_send_pdf") == "1",
            "timeout_ms": int(s.get("page_timeout_seconds") or 60) * 1000, "headless": s.get("headless", "1") == "1",
            "wa_headless": s.get("wa_show_browser") != "1"}


def output_folder(day):
    import report_pipeline
    return os.path.join(report_pipeline.output_root(), "Rep Attendance", day, datetime.now().strftime("%H%M%S"))


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
async def _download(page, cfg, url, day, timeout_ms):
    LIVE.step(0, "open", "active")
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await SA._settle(page)
    if await SA._ensure_logged_in(page, cfg, timeout_ms):
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await SA._settle(page)
    deadline = time.time() + timeout_ms / 1000
    while (await SA._find_field(page, "Date From"))[0] is None:
        if time.time() > deadline:
            raise StepError("open", "The Rep Attendance Report opened but its Date From filter never appeared — check its "
                                    "address on the Rep Attendance screen › Settings.")
        if LIVE.cancelled():
            raise StepError("open", "Stopped.")
        await page.wait_for_timeout(400)
    LIVE.step(0, "open", "done")
    LIVE.step(0, "filters", "active")
    for label in ("Date From", "Date To"):
        try:
            await SA.fill_filter(page, {"label": label, "type": "date", "date_mode": "fixed", "value": day}, day)
        except SA.StepError as e:
            raise StepError("filters", f"{label}: {e.message}")
    LIVE.step(0, "filters", "done")
    LIVE.step(0, "search", "active")
    btn, _l, _seen = await SA._find_button(page, "SEARCH", 10000)
    if btn is None:
        raise StepError("search", "No SEARCH button on the Rep Attendance Report.")
    await btn.click()
    await SA._settle(page, 30000)
    await page.wait_for_timeout(1500)
    LIVE.step(0, "search", "done")
    LIVE.step(0, "export", "active")
    el, _m, seen = await SA._find_button(page, "Excel", timeout_ms, export_mode=True)
    if el is None:
        body = (await page.inner_text("body"))[:4000]
        if any(p in body.lower() for p in ("no data", "no records", "no record found", "no data available")):
            raise StepError("export", f"SFA shows no rep attendance for {day} (nobody has logged in yet?).")
        raise StepError("export", "No Excel icon appeared after SEARCH. Seen: " + ("; ".join(seen[:15]) or "none"))
    dl = await SA._click_and_download(page, el, max(timeout_ms, 120000))
    if dl is None:
        raise StepError("export", "Clicked the Excel icon but no file downloaded.")
    fail = await dl.failure()
    if fail:
        raise StepError("export", f"Download failed: {fail}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    path = os.path.join(DOWNLOAD_DIR, f"RepAttendance_{day}_{datetime.now():%H%M%S}.xlsx")
    await dl.save_as(path)
    LIVE.step(0, "export", "done")
    return path


async def _with_browser(cfg, st, body):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await SA._launch(p, cfg.get("browser", "chrome"), st["headless"])
        _ACTIVE.update(loop=asyncio.get_running_loop(), browser=browser)
        try:
            ctx = await browser.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
            page = await ctx.new_page()
            LIVE.set(phase="logging_in")
            LIVE.step(0, "login", "active")
            try:
                _ok, msg = await SA._login(page, cfg, st["timeout_ms"])
            except Exception as e:
                raise StepError("login", e.message if isinstance(e, SA.StepError) else
                                f"Couldn't reach SFA: {str(e).splitlines()[0]}")
            LIVE.log(msg)
            LIVE.step(0, "login", "done")
            LIVE.set(phase="working")
            return await body(page)
        finally:
            _ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Build + send
# ---------------------------------------------------------------------------
def _build(rid, day, st, as_of=None):
    info = R.get_day(day)
    info["as_of"] = as_of or datetime.now().strftime("%H:%M")
    styles = ["classic", "professional"] if st["style"] == "both" else [st["style"]]
    folder = output_folder(day)
    images, pdfs = [], []
    for style in styles:
        out = RI.build(day, info, style, folder)
        images += out["images"]
        if out["pdf"]:
            pdfs.append(out["pdf"])
    send_style = st["send_style"] if st["style"] == "both" else st["style"]
    R.set_run(rid, style=send_style, images=images, pdf=next((p for p in pdfs if send_style in os.path.basename(p)), ""),
              counts=info["counts"])
    return info, images


def send(rid, wait=True):
    """Sends a run's images (the chosen look) to the chats. Returns {ok, message} (or {busy})."""
    import whatsapp_sender as W
    run = R.get_run(rid)
    if not run:
        return {"ok": False, "message": "That run no longer exists."}
    st = settings()
    if not st["chats"]:
        R.set_run(rid, state="not_sent", message=(run["message"] + " " if run["message"] else "") +
                  "No WhatsApp chats set on the Rep Attendance screen — not sent.")
        return {"ok": False, "message": "No WhatsApp chats set for rep attendance (Rep Attendance › Settings)."}
    imgs = [i for i in run["images"] if i["style"] == run["style"] and os.path.exists(i["path"])]
    if not imgs:
        return {"ok": False, "message": "This run's images are missing — build them again."}
    d = date.fromisoformat(run["day"])
    c = run["counts"]
    caps = {"not_logged_out": f"Rep attendance {d:%d %b %Y} — not logged out yet ({c.get('not_logged_out', 0)})"
                              + (f" · not logged in: {c['not_logged_in']}" if c.get("working") else ""),
            "logged_out": f"Rep attendance {d:%d %b %Y} — logged out ({c.get('logged_out', 0)})"}
    pdf = run["pdf"] if st["send_pdf"] and run["pdf"] and os.path.exists(run["pdf"]) else ""
    state = {"sent": 0, "failed": []}

    async def fn(page):
        for chat in st["chats"]:
            try:
                await W._open_chat(page, chat)
                for im in imgs:
                    await W._send_files(page, [im["path"]], caps.get(im["group"], ""), "photos")
                if pdf:
                    await W._send_files(page, [pdf], "", "document")
                state["sent"] += 1
                W._log_delivery(None, chat, len(imgs) + (1 if pdf else 0), "sent", f"Rep attendance {run['day']}")
                W.WA.log(f"{chat}: rep attendance sent ({len(imgs)} image(s)).")
            except Exception as e:
                msg = str(e).splitlines()[0][:200]
                state["failed"].append(f"{chat}: {msg}")
                W._log_delivery(None, chat, 0, "failed", f"Rep attendance {run['day']} — {msg}")
                W.WA.log(f"{chat}: rep attendance failed — {msg}", "error")
        ok = not state["failed"]
        return {"ok": ok, "message": f"Sent to {state['sent']} of {len(st['chats'])} chat(s)."
                                     + (" " + "; ".join(state["failed"]) if state["failed"] else "")}

    def finish(res):
        sent, total = state["sent"], len(st["chats"])
        R.set_run(rid, state="sent" if sent == total else "partly_sent" if sent else "send_failed",
                  sent_at=datetime.now().isoformat(timespec="seconds") if sent else None,
                  message=(res or {}).get("message", ""))
        try:
            import desktop_notify
            desktop_notify.event("finished" if sent == total else "failed", "attendance",
                                 f"Rep attendance {run['day']} — {'sent' if sent == total else 'not fully sent'}",
                                 (res or {}).get("message", ""), action="rep-attendance")
        except Exception:
            pass

    if not W.WA.start("attendance"):
        return {"ok": False, "busy": True, "message": "WhatsApp is busy with another task — try again in a moment."}

    def go():
        res = W._run("attendance", st["wa_headless"], fn)
        finish(res)
        return res

    if wait:
        return go()
    threading.Thread(target=go, daemon=True, name="wa-attendance").start()
    return {"ok": True, "message": "Sending…"}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def _lane():
    return {"name": "Rep Attendance Report", "status": "running", "message": "", "elapsed": 0, "started": time.time(),
            "steps": {s: "pending" for s in STEPS}}


def _finish(rid, status, msg, result=None):
    R.set_run(rid, status=status, finished_at=datetime.now().isoformat(timespec="seconds"), message=msg,
              log=log_text())
    res = {"ok": status == "done", "status": status, "message": msg, "run_id": rid, **(result or {})}
    LIVE.lane(0, status="done" if status == "done" else "failed" if status == "failed" else "cancelled", message=msg)
    LIVE.set(active=False, phase="done", result=res)
    return res


def run(day=None, trigger="manual", slot="", review=False, send=False, headless=None):
    """Download + read + build; then send (send=True), or wait for approval (review=True)."""
    day = day or date.today().isoformat()
    st = settings()
    if headless is not None:
        st["headless"] = headless
    cfg = sfa_config.load_config()
    if not AS.acquire_lock(f"rep attendance ({datetime.now():%H:%M})"):
        holder = AS.lock_holder() or {}
        msg = f"Another SFA run is already in progress ({holder.get('owner', 'unknown')})."
        LIVE.reset(kind="attendance", trigger=trigger, slot=slot)
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "busy", "message": msg})
        return {"ok": False, "status": "busy", "message": msg}
    locked = [True]

    def unlock():
        if locked[0]:
            locked[0] = False
            AS.release_lock()

    rid = None
    try:
        LIVE.reset(kind="attendance", trigger=trigger, slot=slot, report_date=day, steps=STEPS, lanes=[_lane()])
        rid = R.start_run(day, trigger, slot)
        LIVE.set(run_id=rid)
        if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
            unlock()
            return _finish(rid, "failed", "SFA login isn't set up — Settings › SFA Connection.")
        LIVE.log(f"Rep attendance for {day}: downloading SFA's Rep Attendance Report (read only).")
        try:
            path = asyncio.run(_with_browser(cfg, st, lambda page: _download(page, cfg, st["url"], day, st["timeout_ms"])))
        except StepError as e:
            LIVE.step(0, e.step, "failed")
            unlock()
            if LIVE.cancelled():
                return _finish(rid, "cancelled", "Stopped by you.")
            return _finish(rid, "failed", e.message)
        unlock()                                   # SFA is done; WhatsApp sending doesn't need the SFA lock
        return _after_download(rid, day, path, st, review, send, source="sfa")
    except Exception as e:
        unlock()
        if LIVE.cancelled():
            return _finish(rid, "cancelled", "Stopped by you.") if rid else {"ok": False, "status": "cancelled"}
        if rid:
            return _finish(rid, "failed", f"Unexpected error: {type(e).__name__}: {e}")
        raise


def _after_download(rid, day, path, st, review, send_now, source):
    LIVE.step(0, "read", "active")
    try:
        parsed = R.read_excel(path)
    except R.ReadError as e:
        LIVE.step(0, "read", "failed")
        return _finish(rid, "failed", str(e))
    if parsed["day"] != day:
        LIVE.step(0, "read", "failed")
        return _finish(rid, "failed", f"SFA's file is for {parsed['day']}, not {day} — the dates didn't take. Nothing was saved.")
    saved = R.save_day(parsed, source, path)
    LIVE.step(0, "read", "done")
    LIVE.log(f"{saved['reps']} rep(s) logged in" + (f"; kept {saved['kept_corrections']} of your corrections"
                                                     if saved["kept_corrections"] else "") + ".")
    LIVE.step(0, "build", "active")
    info, images = _build(rid, day, st)
    LIVE.step(0, "build", "done")
    c = info["counts"]
    summary = (f"{c['logged_in']} logged in · {c['not_logged_out']} not logged out · {c['logged_out']} logged out"
               + (f" · {c['not_logged_in']} of {c['working']} working territories not logged in" if c["working"] else ""))
    LIVE.log(summary)
    if not send_now:
        R.set_run(rid, state="built")
        LIVE.step(0, "send", "skipped")
        return _finish(rid, "done", summary + ". Images ready — not sent.")
    if review:
        R.set_run(rid, state="pending_review")
        LIVE.step(0, "send", "skipped")
        return _finish(rid, "done", summary + ". Waiting for your approval before sending.",
                       {"needs_approval": True, "approval_action": "rep-attendance"})
    LIVE.step(0, "send", "active")
    res = send(rid, wait=True)
    LIVE.step(0, "send", "done" if res.get("ok") else "failed")
    return _finish(rid, "done" if res.get("ok") else "failed", summary + ". " + res.get("message", ""))


def rebuild(day, trigger="manual"):
    """Images again from what Chimera has for the day (after an upload or corrections). No SFA."""
    if not R.get_day(day)["rows"]:
        return {"ok": False, "message": f"Chimera has no rep attendance for {day} yet."}
    st = settings()
    rid = R.start_run(day, trigger)
    _info, images = _build(rid, day, st)
    R.set_run(rid, status="done", state="built", finished_at=datetime.now().isoformat(timespec="seconds"),
              message="Images built from Chimera's copy (no SFA).")
    return {"ok": True, "run_id": rid, "message": "Images ready."}


def import_file(path):
    """An exported file you pick (e.g. an older day). Returns {ok, day, message}."""
    try:
        parsed = R.read_excel(path)
    except R.ReadError as e:
        return {"ok": False, "message": str(e)}
    saved = R.save_day(parsed, "file", path)
    res = rebuild(parsed["day"], trigger="file")
    return {"ok": True, "day": parsed["day"], "run_id": res.get("run_id"),
            "message": f"{parsed['day']}: {saved['reps']} rep(s) read" + (f", {saved['kept_corrections']} of your corrections kept"
                                                                           if saved["kept_corrections"] else "") + "."}


def start_background(**kw):
    import itinerary_automation, loading_automation, sequence_automation
    if LIVE.snapshot().get("active") or SA.LIVE.snapshot().get("active") or loading_automation.LIVE.snapshot().get("active") \
            or itinerary_automation.LIVE.snapshot().get("active") or sequence_automation.LIVE.snapshot().get("active"):
        return {"ok": False, "message": "Another SFA run is already in progress."}
    LIVE.reset(kind="attendance", trigger=kw.get("trigger", "manual"))

    def guarded():
        try:
            res = run(**kw)
        except Exception as e:
            res = {"ok": False, "status": "failed", "message": f"Unexpected error: {type(e).__name__}: {e}"}
        if LIVE.snapshot().get("active"):
            LIVE.set(active=False, phase="done", result=res)

    t = threading.Thread(target=guarded, daemon=True, name="chimera-attendance")
    _ACTIVE["thread"] = t
    t.start()
    return {"ok": True, "message": "Started."}


def force_stop(timeout=10):
    was = bool(LIVE.snapshot().get("active")) or _ACTIVE["browser"] is not None
    if was:
        LIVE.cancel()
        LIVE.log("Stopped immediately by the user.", level="warn")
    loop, browser = _ACTIVE["loop"], _ACTIVE["browser"]
    if loop and browser:
        try:
            asyncio.run_coroutine_threadsafe(browser.close(), loop).result(timeout)
        except Exception:
            pass
    t = _ACTIVE.get("thread")
    if LIVE.snapshot().get("active") and (t is None or not t.is_alive()):
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "cancelled", "message": "Stopped by you."})
    return was


def log_text(s=None):
    s = s or LIVE.snapshot()
    out = [f"Chimera Rep Attendance — run #{s.get('run_id') or '-'} · {s.get('trigger', '')} · {s.get('report_date', '')}"]
    res = s.get("result") or {}
    if res:
        out.append(f"Result: {str(res.get('status', '')).upper()} — {res.get('message', '')}")
    out.append("")
    for x in s.get("log") or []:
        out.append(f"{x['t']} {x['level'].upper():5} {x['msg']}")
    return "\n".join(out)
