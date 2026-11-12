"""
distributor_sync.py
Keeps the Distributor Loadings list up to date from SFA. Read only.

  Users page (default https://lakmee.salespro.lk/it/users) -> User Type =
  DISTRIBUTOR, Status = Active -> SEARCH -> Excel; then the same with
  Status = Inactive. The export itself has no status column, which is why
  it is downloaded twice. Every distributor is matched by its SFA user code
  (DB001…) and merged into the list (loading_store.apply_sfa_list):
  new ones are added 'On request' with their Distributor filter set to
  their SFA name, vacant ones are tagged, inactive ones are kept but can't
  be prepared or confirmed.

Runs on demand, from a schedule job ("Distributor list update"), or from
an Excel file the user picked (the same export, downloaded by hand).
Progress shows in Distributor Loadings' live window (loading_automation.LIVE).
"""

import asyncio
import os
import re
import tempfile
import threading
import time
from datetime import datetime

import automation_store as AS
import loading_automation as LA
import loading_store as LS
import sfa_automation as SA
import sfa_config

DEFAULT_USERS_URL = "https://lakmee.salespro.lk/it/users"
STEPS = ["login", "open", "filters", "search", "export", "read"]
STEP_LABELS = {"login": "Log in", "open": "Open Users", "filters": "Filters", "search": "Search",
               "export": "Excel", "read": "Read list"}
PASSES = [("active", "Active"), ("inactive", "Inactive")]
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "downloads", "users")

# Export column (as SFA labels it) -> field
COLUMNS = {"user code": "code", "name": "name", "user name": "username", "telephone": "phone", "address": "address",
           "district": "district", "city": "city", "user type": "user_type", "rsm": "rsm", "asm": "asm", "ase": "ase",
           "region": "region", "area": "area", "sa": "sa", "supervisor area": "sa", "territory": "territory"}


class SyncError(Exception):
    def __init__(self, step, message):
        super().__init__(message)
        self.step, self.message = step, message


# ---------------------------------------------------------------------------
# Reading the export
# ---------------------------------------------------------------------------
def read_users_excel(path, status=""):
    """Rows of a User Profile export (header row found by 'USER CODE' + 'NAME'); only User Type DISTRIBUTOR."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise SyncError("read", f"That file isn't an Excel workbook Chimera can open ({type(e).__name__}).")
    try:
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    hdr_i, cols = None, {}
    for i, r in enumerate(rows[:40]):
        names = [str(c).strip().lower() if c is not None else "" for c in r]
        if "user code" in names and "name" in names:
            hdr_i = i
            cols = {COLUMNS[n]: k for k, n in enumerate(names) if n in COLUMNS}
            break
    if hdr_i is None:
        raise SyncError("read", "This doesn't look like SFA's User Profile export — no 'USER CODE' / 'NAME' header row.")
    out = []

    def cell(r, f):
        k = cols.get(f)
        v = r[k] if k is not None and k < len(r) else None
        v = "" if v is None else str(v).strip()
        if f == "phone" and v.endswith(".0"):
            v = v[:-2]
        return "" if v in ("-", "–") else v

    for r in rows[hdr_i + 1:]:
        if not r or not any(c not in (None, "") for c in r):
            continue
        code, name = cell(r, "code"), cell(r, "name")
        if not code or not name:
            continue
        ut = cell(r, "user_type")
        if ut and ut.upper() != "DISTRIBUTOR":
            continue
        out.append({f: cell(r, f) for f in cols} | {"status": status})
    return out


def import_excel(path, trigger="file"):
    """A User Profile export picked by the user. Its status can't be known (the export has no status column),
    so known distributors keep their status and new ones are treated as active."""
    rows = read_users_excel(path, status="")
    if not rows:
        return {"ok": False, "message": "No distributors in that file (User Type DISTRIBUTOR)."}
    known = {d["code"].upper(): d.get("sfa_status") or "" for d in LS.list_distributors() if d["code"]}
    for r in rows:
        r["status"] = known.get(r["code"].upper()) or "active"
    rid = LS.start_run("sync", trigger)
    res = LS.apply_sfa_list(rows, complete=False)
    msg = "From the Excel file: " + LS.sync_summary_text(res) + " Active/inactive comes from SFA's next update."
    LS.finish_run(rid, "done", msg, "")
    return {"ok": True, "message": msg, "result": res}


# ---------------------------------------------------------------------------
# Getting it from SFA
# ---------------------------------------------------------------------------
async def _export_pass(context, cfg, url, idx, status_label, opts):
    page = await context.new_page()
    try:
        LA.LIVE.step(idx, "open", "active")
        await page.goto(url, wait_until="domcontentloaded", timeout=opts["timeout_ms"])
        await SA._settle(page)
        if await SA._ensure_logged_in(page, cfg, opts["timeout_ms"]):
            await page.goto(url, wait_until="domcontentloaded", timeout=opts["timeout_ms"])
            await SA._settle(page)
        deadline = time.time() + opts["timeout_ms"] / 1000
        while (await SA._find_field(page, "User Type"))[0] is None:
            if time.time() > deadline:
                raise SyncError("open", "The Users page opened but its 'User Type' filter never appeared — check the "
                                        "Users page address in Distributor Loadings › Settings.")
            if LA.LIVE.cancelled():
                raise LA.Cancelled()
            await page.wait_for_timeout(400)
        LA.LIVE.step(idx, "open", "done")
        LA.LIVE.step(idx, "filters", "active")
        for f in ({"label": "User Type", "type": "dropdown", "value": "DISTRIBUTOR"},
                  {"label": "Status", "type": "dropdown", "value": status_label}):
            try:
                await SA.fill_filter(page, f, datetime.now().date().isoformat())
            except SA.StepError as e:
                raise SyncError("filters", e.message)
        LA.LIVE.step(idx, "filters", "done")
        LA.LIVE.step(idx, "search", "active")
        btn, _lbl, seen = await SA._find_button(page, "SEARCH", 10000)
        if btn is None:
            raise SyncError("search", "No SEARCH button found. Buttons on the page: " + ("; ".join(seen[:12]) or "none"))
        await btn.click()
        await SA._settle(page, 15000)
        await page.wait_for_timeout(1500)
        LA.LIVE.step(idx, "search", "done")
        LA.LIVE.step(idx, "export", "active")
        el, _m, seen = await SA._find_button(page, "Excel", opts["timeout_ms"], export_mode=True)
        if el is None:
            body = await page.inner_text("body")
            if SA.NO_DATA_PATTERNS.search(body):
                LA.LIVE.step(idx, "export", "skipped")
                return None          # no distributors with this status
            raise SyncError("export", "No Excel icon appeared after SEARCH. Buttons Chimera can see: "
                                      + ("; ".join(seen[:20]) or "none"))
        dl = await SA._click_and_download(page, el, opts["timeout_ms"])
        if dl is None:
            raise SyncError("export", "Clicked the Excel icon but no file downloaded.")
        fail = await dl.failure()
        if fail:
            raise SyncError("export", f"Download failed: {fail}")
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        path = os.path.join(DOWNLOAD_DIR, f"distributors_{status_label.lower()}_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
        await dl.save_as(path)
        LA.LIVE.step(idx, "export", "done")
        return path
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def _run_async(cfg, url, opts):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        LA.LIVE.set(phase="logging_in")
        browser = await SA._launch(p, cfg.get("browser", "chrome"), opts["headless"])
        LA._ACTIVE.update(loop=asyncio.get_running_loop(), browser=browser)
        try:
            context = await browser.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
            page = await context.new_page()
            for i in range(len(PASSES)):
                LA.LIVE.step(i, "login", "active")
            try:
                _ok, msg = await SA._login(page, cfg, opts["timeout_ms"])
            except Exception as e:
                msg = e.message if isinstance(e, SA.StepError) else f"Couldn't reach SFA: {str(e).splitlines()[0]}"
                for i in range(len(PASSES)):
                    LA.LIVE.step(i, "login", "failed")
                    LA.LIVE.lane(i, status="failed", message=msg)
                raise SyncError("login", msg)
            LA.LIVE.log(msg)
            for i in range(len(PASSES)):
                LA.LIVE.step(i, "login", "done")
            await page.close()
            LA.LIVE.set(phase="syncing")
            files = {}
            for i, (status, label) in enumerate(PASSES):      # one after the other: same page, same filters
                if LA.LIVE.cancelled():
                    break
                LA.LIVE.lane(i, status="running", started=time.time())
                try:
                    files[status] = await _export_pass(context, cfg, url, i, label, opts)
                except LA.Cancelled:
                    LA.LIVE.lane(i, status="cancelled", message="Stopped.")
                    break
                except SyncError as e:
                    LA.LIVE.step(i, e.step, "failed")
                    LA.LIVE.lane(i, status="failed", message=e.message)
                    LA.LIVE.log(e.message, lane=PASSES[i][1], level="error")
                    files[status] = e
            return files
        finally:
            LA._ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:
                pass


def settings():
    s = AS.get_automation_settings()
    return {"url": s.get("users_url") or DEFAULT_USERS_URL,
            "timeout_ms": int(s.get("page_timeout_seconds") or 60) * 1000,
            "headless": s.get("headless", "1") == "1"}


def run_sync(trigger="manual", headless=None):
    st = settings()
    if headless is not None:
        st["headless"] = headless
    cfg = sfa_config.load_config()
    lanes = [{"name": f"{label} distributors", "status": "queued", "message": "", "steps": {s: "pending" for s in STEPS},
              "started": None, "elapsed": 0} for _s, label in PASSES]
    if not AS.acquire_lock(f"distributor list ({datetime.now():%H:%M})"):
        holder = AS.lock_holder() or {}
        msg = f"Another SFA run is already in progress ({holder.get('owner', 'unknown')})."
        LA.LIVE.reset(kind="sync", trigger=trigger)
        LA.LIVE.set(active=False, phase="done", result={"ok": False, "status": "busy", "message": msg})
        return {"ok": False, "status": "busy", "message": msg}
    LA.LIVE.reset(kind="sync", trigger=trigger)
    LA.LIVE.set(lanes=lanes, steps=STEPS)
    rid = LS.start_run("sync", trigger)
    LA.LIVE.set(run_id=rid)

    def finish(status, msg):
        LS.finish_run(rid, status, msg, LA.log_text())
        LA.LIVE.set(active=False, phase="done",
                    result={"ok": status == "done", "status": status, "message": msg, "run_id": rid})
        LA.LIVE.log(msg, level="info" if status == "done" else "warn")
        AS.release_lock()
        return {"ok": status == "done", "status": status, "message": msg, "run_id": rid}

    try:
        if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
            return finish("failed", "SFA login isn't set up — Settings › SFA Connection.")
        LA.LIVE.log(f"Reading the distributor list from SFA's Users page ({st['url']}) — Active, then Inactive. "
                    "Nothing is changed in SFA.")
        opts = {"timeout_ms": st["timeout_ms"], "headless": st["headless"]}
        try:
            files = asyncio.run(_run_async(cfg, st["url"], opts))
        except SyncError as e:
            return finish("failed", e.message)
        if LA.LIVE.cancelled():
            return finish("cancelled", "Stopped — the distributor list wasn't changed.")
        bad = [s for s, f in files.items() if isinstance(f, Exception)]
        if bad or len(files) < len(PASSES):
            return finish("failed", "The distributor list wasn't changed — " +
                          ("; ".join(f"{s}: {files[s].message}" for s in bad) or "a download didn't finish") + ".")
        rows = []
        for i, (status, label) in enumerate(PASSES):
            LA.LIVE.step(i, "read", "active")
            got = read_users_excel(files[status], status=status) if files[status] else []
            rows += got
            LA.LIVE.step(i, "read", "done")
            LA.LIVE.lane(i, status="done", message=f"{len(got)} {label.lower()} distributor(s).",
                         elapsed=round(time.time() - (LA.LIVE.snapshot()["lanes"][i]["started"] or time.time()), 1))
        if not rows:
            return finish("failed", "SFA listed no distributors at all — the list wasn't changed (check the Users "
                                    "page filters).")
        res = LS.apply_sfa_list(rows, complete=True)
        if res["added"]:
            LA.LIVE.log("New: " + ", ".join(res["added"][:30]) + ("…" if len(res["added"]) > 30 else ""))
        for r in res["renamed"][:20]:
            LA.LIVE.log("Renamed in SFA: " + r)
        return finish("done", "Distributor list updated: " + LS.sync_summary_text(res))
    except SyncError as e:
        return finish("failed", e.message)
    except Exception as e:
        return finish("failed", f"Unexpected error: {type(e).__name__}: {e}")


def start_background(**kw):
    import itinerary_automation
    if LA.LIVE.snapshot().get("active") or SA.LIVE.snapshot().get("active") \
            or itinerary_automation.LIVE.snapshot().get("active"):
        return {"ok": False, "message": "Another SFA run is already in progress."}
    LA.LIVE.reset(kind="sync", trigger=kw.get("trigger", "manual"))
    threading.Thread(target=run_sync, kwargs=kw, daemon=True, name="chimera-distributor-sync").start()
    return {"ok": True, "message": "Started."}
