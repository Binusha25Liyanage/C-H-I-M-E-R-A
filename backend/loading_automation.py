"""
loading_automation.py
Distributor Loadings in SFA — up to 12 distributors at a time, one browser
tab each, after a single login.

PREPARE (read only — never presses Confirm):
  View Loadings -> the distributor's filters -> SEARCH -> the pending loading
  (the ✓ in the CONFIRM column; a row saying "Confirmed" is done already)
  -> CONFIRM LOADING page -> read loading number, rep, date/time, every
  product and the totals, and take a screenshot. The result waits in
  Chimera for the user's approval.

CONFIRM (only loadings the user approved):
  reopen that CONFIRM LOADING page -> check it still matches what was
  approved (loading number, quantities, totals) -> press the green Confirm
  -> answer "Are you sure?" with Yes/OK -> search View Loadings again and
  check the row now says Confirmed.
"""

import asyncio
import json
import os
import re
import threading
import time
import uuid
from datetime import date, datetime
from urllib.parse import urljoin

import automation_store as AS
import loading_store as LS
import sfa_config
import sfa_automation as SA

PREPARE_STEPS = ["login", "open", "filters", "search", "find", "read"]
CONFIRM_STEPS = ["login", "open", "check", "confirm", "verify"]
STEP_LABELS = {"login": "Log in", "open": "Open", "filters": "Filters", "search": "Search", "find": "Find pending",
               "read": "Read loading", "check": "Check", "confirm": "Confirm", "verify": "Verify"}
SHOT_ROOT = os.path.join(os.path.expanduser("~"), "Chimera", "screenshots", "loadings")

LIVE = SA.LiveState()
_ACTIVE = {"loop": None, "browser": None}


class StepError(Exception):
    def __init__(self, step, message):
        super().__init__(message)
        self.step, self.message = step, message


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Page readers
# ---------------------------------------------------------------------------
_RESULTS_JS = r"""
([token]) => {
  // The View Loadings results table: every row, and which one is pending
  // (a clickable ✓ in the CONFIRM column instead of the word "Confirmed").
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  for (const t of [...document.querySelectorAll('table')].filter(vis)) {
    const heads = [...t.querySelectorAll('thead th, thead td')];
    const hs = (heads.length ? heads : [...(t.querySelector('tr') || {children: []}).children]).map(h => norm(h.innerText).toLowerCase());
    const col = re => hs.findIndex(h => re.test(h));
    const iC = col(/^confirm$/), iN = col(/loading\s*(no|number)/), iR = col(/rep\s*name/), iD = col(/loading\s*date/),
          iT = col(/loading\s*time/), iS = col(/dis\w*\s*confirmation/);
    if (iC < 0) continue;
    const body = [...t.querySelectorAll('tbody tr')].filter(r => r.children.length > iC);
    const rows = [];
    let pending = -1;
    body.forEach((r, k) => {
      const c = r.children, cell = c[iC];
      const txt = norm(cell.innerText);
      const clickable = cell.querySelector('a, button, [role=button], i, svg, img, span[class*=check], [onclick]');
      const isPending = !!clickable && !/confirmed/i.test(txt);
      rows.push({number: iN >= 0 ? norm(c[iN].innerText) : '', rep: iR >= 0 ? norm(c[iR].innerText) : '',
                 date: iD >= 0 ? norm(c[iD].innerText) : '', time: iT >= 0 ? norm(c[iT].innerText) : '',
                 status: iS >= 0 ? norm(c[iS].innerText) : '', confirm_cell: txt || (clickable ? '✓' : ''), pending: isPending});
      if (isPending && pending < 0) {
        pending = k;
        (clickable.closest('a, button, [role=button]') || clickable).setAttribute('data-chimera-pending', token);
      }
    });
    return {table: true, rows, pending};
  }
  const empty = /no (data|records?)/i.test(document.body.innerText || '');
  return {table: false, rows: [], pending: -1, empty};
}
"""

_READ_CONFIRM_JS = r"""
() => {
  // CONFIRM LOADING page: the header fields and the product table with totals.
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const valOf = el => { if (!el) return '';
    if (el.matches('input, textarea, select')) return norm(el.value);
    const inner = el.querySelector('input, textarea, select');            // product cells hold an input box
    return norm(inner ? inner.value : el.innerText); };
  const heading = [...document.querySelectorAll('h1, h2, h3, h4')].map(h => norm(h.innerText)).find(t => /confirm\s+loading/i.test(t)) || '';
  const header = {};
  for (const l of document.querySelectorAll('label')) {
    const k = norm(l.innerText).toLowerCase();
    let inp = l.control || null;
    if (!inp) { let n = l.nextElementSibling; while (n && !n.matches('input, textarea, select') && !n.querySelector('input')) n = n.nextElementSibling;
                inp = n ? (n.matches('input, textarea, select') ? n : n.querySelector('input')) : (l.parentElement && l.parentElement.querySelector('input')); }
    if (/^date$/.test(k)) header.date = valOf(inp);
    else if (/^time$/.test(k)) header.time = valOf(inp);
    else if (/loading\s*(no|number)/.test(k)) header.loading_number = valOf(inp);
    else if (/sales\s*rep|^rep$/.test(k)) header.rep = valOf(inp);
  }
  const keyOf = h => { h = h.toLowerCase();
    if (/product\s*code|^code$/.test(h)) return 'code'; if (/^product/.test(h)) return 'product';
    if (/price/.test(h)) return 'price'; if (/stock/.test(h)) return 'stock'; if (/accepted|qty|quantity/.test(h)) return 'qty';
    if (/volume/.test(h)) return 'volume'; if (/amount|value/.test(h)) return 'amount'; return h; };
  let products = [], totals = {}, columns = [];
  for (const t of document.querySelectorAll('table')) {
    const hs = [...t.querySelectorAll('thead th, thead td')].map(h => norm(h.innerText));
    if (!hs.some(h => /product/i.test(h)) || !hs.some(h => /accepted|quantity/i.test(h))) continue;
    columns = hs; const keys = hs.map(keyOf);
    for (const r of t.querySelectorAll('tbody tr')) {
      const cells = [...r.children];
      if (/^total$/i.test(valOf(cells[0]))) {         // totals row: values are in the last cells
        const vals = cells.map(valOf); const n = vals.length;
        const qtyK = keys.indexOf('qty'), volK = keys.indexOf('volume'), amtK = keys.indexOf('amount');
        // the totals row may span columns: take its last numbers in order qty, volume, amount
        const nums = vals.slice(1).filter(v => v !== '');
        if (nums.length >= 3) { totals.qty = nums[nums.length - 3]; totals.volume = nums[nums.length - 2]; totals.amount = nums[nums.length - 1]; }
        else if (amtK >= 0) { totals.amount = vals[n - 1]; }
        continue;
      }
      if (cells.length < keys.length) continue;
      const row = {}; keys.forEach((k, i) => row[k] = valOf(cells[i]));
      if (row.product || row.code) products.push(row);
    }
    break;
  }
  return {heading, header, columns, products, totals, url: location.href};
}
"""

_POPUP_YES_JS = r"""
([token]) => {
  // The "Are you sure?" confirmation inside the page (SweetAlert / Bootstrap modal / dialog role).
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  const boxes = [...document.querySelectorAll('.swal-modal, .swal2-popup, .sweet-alert, .modal.show, .modal.in, [role=dialog], [role=alertdialog], .p-dialog, .p-confirm-dialog')].filter(vis);
  for (const b of boxes) {
    const btns = [...b.querySelectorAll('button, a, [role=button], .swal-button, .swal2-confirm')].filter(vis);
    const yes = btns.find(x => /^(yes|ok|okay|confirm|yes,?\s.*|sure|proceed)$/i.test((x.innerText || x.value || '').trim()))
             || btns.find(x => /swal-button--confirm|swal2-confirm|btn-primary|btn-success|p-confirm-dialog-accept/.test(x.className || ''));
    if (yes) { yes.setAttribute('data-chimera-yes', token); return (yes.innerText || '').trim() || 'OK'; }
  }
  return null;
}
"""


def _num(v):
    try:
        return round(float(str(v).replace(",", "").strip() or 0), 3)
    except ValueError:
        return None


def _fingerprint(loading):
    """What must not change between approval and confirming."""
    return {"number": (loading.get("loading_number") or "").strip(),
            "qty": [(p.get("code") or p.get("product"), _num(p.get("qty"))) for p in loading.get("products", [])],
            "total_qty": _num((loading.get("totals") or {}).get("qty")),
            "total_amount": _num((loading.get("totals") or {}).get("amount"))}


def _differences(approved, now):
    a, b = _fingerprint(approved), _fingerprint(now)
    out = []
    if a["number"] != b["number"]:
        out.append(f"loading number is now {b['number'] or 'missing'} (approved {a['number']})")
    if a["qty"] != b["qty"]:
        changed = [f"{x[0]}: {x[1]} → {y[1]}" for x, y in zip(a["qty"], b["qty"]) if x != y]
        out.append("quantities changed (" + ", ".join(changed[:5]) + ")" if changed else "the product list changed")
    for k, label in (("total_qty", "total quantity"), ("total_amount", "total amount")):
        if a[k] is not None and b[k] is not None and a[k] != b[k]:
            out.append(f"{label} {a[k]:,} → {b[k]:,}")
    return out


# ---------------------------------------------------------------------------
# Shared browser steps
# ---------------------------------------------------------------------------
async def _shot(page, run_id, name, full=False):
    try:
        folder = os.path.join(SHOT_ROOT, f"run_{run_id}")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, re.sub(r"[^\w\- ]", "_", name) + ".png")
        await page.screenshot(path=path, full_page=full)
        return path
    except Exception:
        return None


async def _open_and_search(page, cfg, url, distributor, idx, timeout_ms, lane_steps=True):
    """View Loadings -> filters -> SEARCH. Returns the results-table reading."""
    if lane_steps:
        LIVE.step(idx, "open", "active")
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await SA._settle(page)
    if await SA._ensure_logged_in(page, cfg, timeout_ms):
        LIVE.log("Session had expired in this tab — logged in again.", lane=distributor["name"])
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await SA._settle(page)
    first = next((f["label"] for f in distributor["filters"]), None)
    if first:
        deadline = time.time() + timeout_ms / 1000
        while (await SA._find_field(page, first))[0] is None:
            if time.time() > deadline:
                raise StepError("open", f"View Loadings opened but the '{first}' filter never appeared — check the "
                                        "View Loadings URL in Distributor Loadings settings.")
            if LIVE.cancelled():
                raise Cancelled()
            await page.wait_for_timeout(400)
    if lane_steps:
        LIVE.step(idx, "open", "done")
        LIVE.step(idx, "filters", "active")
    today = date.today().isoformat()
    for f in distributor["filters"]:
        if LIVE.cancelled():
            raise Cancelled()
        try:
            v = await SA.fill_filter(page, f, today)
        except SA.StepError as e:
            raise StepError("filters", e.message)
        LIVE.log(f"{f['label']} = {v if not isinstance(v, list) else ', '.join(v)}", lane=distributor["name"])
    if lane_steps:
        LIVE.step(idx, "filters", "done")
        LIVE.step(idx, "search", "active")
    btn, _lbl, seen = await SA._find_button(page, "SEARCH", 10000)
    if btn is None:
        raise StepError("search", "No SEARCH button found. Buttons on the page: " + ("; ".join(seen[:12]) or "none"))
    await btn.click()
    # wait for the results table (or a "no data" message)
    deadline = time.time() + max(20, timeout_ms / 1000)
    res = None
    while time.time() < deadline:
        await page.wait_for_timeout(700)
        try:
            res = await page.evaluate(_RESULTS_JS, [uuid.uuid4().hex[:8]])
        except Exception:
            continue   # page still navigating
        if res["table"] or res.get("empty"):
            break
        if LIVE.cancelled():
            raise Cancelled()
    if lane_steps:
        LIVE.step(idx, "search", "done")
    return res or {"table": False, "rows": [], "pending": -1}


async def _read_confirm_page(page, timeout_ms):
    deadline = time.time() + timeout_ms / 1000
    data = None
    while time.time() < deadline:
        try:
            data = await page.evaluate(_READ_CONFIRM_JS)
            if data["heading"] and data["products"]:
                return data
        except Exception:
            pass   # page still navigating
        await page.wait_for_timeout(500)
    return data


def _loading_from_page(d):
    h = d.get("header") or {}
    return {"loading_number": h.get("loading_number", ""), "rep": h.get("rep", ""),
            "loading_date": h.get("date", ""), "loading_time": h.get("time", ""),
            "products": d.get("products") or [], "totals": d.get("totals") or {}}


# ---------------------------------------------------------------------------
# PREPARE
# ---------------------------------------------------------------------------
def _lane(d, steps):
    return {"distributor_id": d["id"], "name": d["name"], "status": "queued", "step": None,
            "steps": {s: "pending" for s in steps}, "message": "", "started": None, "elapsed": 0,
            "loading_number": "", "item_id": None, "screenshot": None}


async def _prepare_lane(context, cfg, url, d, idx, opts, sem):
    async with sem:
        if LIVE.cancelled():
            LIVE.lane(idx, status="cancelled", message="Stopped before it started.")
            return
        LIVE.lane(idx, status="running", started=time.time())
        page = await context.new_page()
        try:
            res = await _open_and_search(page, cfg, url, d, idx, opts["timeout_ms"])
            LIVE.step(idx, "find", "active")
            if res["pending"] < 0:
                done = [r for r in res["rows"] if not r["pending"]]
                msg = ("No pending loading — the latest is already confirmed"
                       + (f" ({done[0]['number']})" if done else "") + "." if res["rows"] else
                       "No loadings found for these filters.")
                LIVE.step(idx, "find", "done")
                LIVE.step(idx, "read", "skipped")
                iid = LS.add_item(opts["run_id"], d, "nothing_pending", message=msg)
                LIVE.lane(idx, status="nothing", message=msg, item_id=iid)
                LIVE.log(msg, lane=d["name"])
                return
            row = res["rows"][res["pending"]]
            LIVE.log(f"Pending loading {row['number'] or ''} ({row['rep']}) — opening it.", lane=d["name"])
            new_page = None
            for attempt in range(2):
                el = page.locator("[data-chimera-pending]").first
                if not await el.count():
                    await page.evaluate(_RESULTS_JS, [uuid.uuid4().hex[:8]])   # mark it again (table redrawn)
                    el = page.locator("[data-chimera-pending]").first
                try:
                    async with context.expect_page(timeout=2500) as pinfo:
                        await el.click(timeout=6000)
                    new_page = await pinfo.value
                    break
                except Exception:
                    if "confirm" in page.url.lower() or await page.locator("text=/confirm\\s+loading/i").count():
                        break      # opened in the same tab
                    if attempt:
                        raise StepError("find", "Couldn't click the ✓ of the pending loading.")
            target = new_page or page
            LIVE.step(idx, "find", "done")
            LIVE.step(idx, "read", "active")
            data = await _read_confirm_page(target, opts["timeout_ms"])
            if not data or not data.get("heading"):
                raise StepError("read", "Clicked the ✓ but the CONFIRM LOADING page didn't open.")
            if not data["products"]:
                raise StepError("read", "CONFIRM LOADING opened but no products could be read.")
            loading = _loading_from_page(data)
            if not loading["loading_number"]:
                loading["loading_number"] = row["number"]
            shot = await _shot(target, opts["run_id"], f"{idx + 1:02d} {d['name']} - confirm page", full=True)
            iid = LS.add_item(opts["run_id"], d, "pending_approval", confirm_url=data["url"], screenshot=shot or "",
                              message="Read from SFA — waiting for your approval.", **loading)
            LIVE.step(idx, "read", "done")
            n = len(loading["products"])
            LIVE.lane(idx, status="ready", item_id=iid, loading_number=loading["loading_number"], screenshot=shot,
                      message=f"{loading['loading_number']} · {n} products · total {loading['totals'].get('amount', '?')} — waiting for approval")
            LIVE.log(f"Read {loading['loading_number']}: {n} products, total amount {loading['totals'].get('amount', '?')}. "
                     "Nothing was confirmed.", lane=d["name"])
            if new_page:
                await new_page.close()
        except Cancelled:
            LIVE.lane(idx, status="cancelled", message="Stopped.")
        except Exception as e:
            msg = e.message if isinstance(e, StepError) else f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
            step = e.step if isinstance(e, StepError) else (LIVE.snapshot()["lanes"][idx].get("step") or "open")
            if LIVE.cancelled():
                LIVE.lane(idx, status="cancelled", message="Stopped.")
            else:
                shot = await _shot(page, opts["run_id"], f"{idx + 1:02d} {d['name']} - failed")
                LIVE.step(idx, step, "failed")
                iid = LS.add_item(opts["run_id"], d, "failed", message=msg, screenshot=shot or "")
                LIVE.lane(idx, status="failed", message=msg, item_id=iid, screenshot=shot)
                LIVE.log(msg, lane=d["name"], level="error")
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def _with_browser(cfg, opts, body):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        LIVE.set(phase="logging_in")
        LIVE.log(f"Starting {cfg.get('browser', 'chrome')} ({'hidden' if opts['headless'] else 'visible'}).")
        browser = await SA._launch(p, cfg.get("browser", "chrome"), opts["headless"])
        _ACTIVE.update(loop=asyncio.get_running_loop(), browser=browser)
        try:
            context = await browser.new_context(viewport={"width": 1500, "height": 950})
            page = await context.new_page()
            lanes = LIVE.snapshot()["lanes"]
            for i in range(len(lanes)):
                LIVE.step(i, "login", "active")
            try:
                _ok, msg = await SA._login(page, cfg, opts["timeout_ms"])
            except Exception as e:
                msg = e.message if isinstance(e, SA.StepError) else f"Couldn't reach SFA: {str(e).splitlines()[0]}"
                for i in range(len(lanes)):
                    LIVE.step(i, "login", "failed")
                    LIVE.lane(i, status="failed", message=msg)
                LIVE.log(msg, level="error")
                return False, msg
            LIVE.log(msg)
            for i in range(len(lanes)):
                LIVE.step(i, "login", "done")
            await page.close()
            await body(context)
            return True, ""
        finally:
            _ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:
                pass


def _settings():
    s = AS.get_automation_settings()
    return {"url": s.get("loading_url") or LS.DEFAULT_URL,
            "tabs": max(1, min(LS.MAX_TABS, int(s.get("loading_tabs") or LS.MAX_TABS))),
            "timeout_ms": int(s.get("page_timeout_seconds") or 60) * 1000,
            "headless": s.get("headless", "1") == "1"}


def _begin(kind, trigger, lanes):
    if not AS.acquire_lock(f"{kind} loadings ({datetime.now():%H:%M})"):
        holder = AS.lock_holder() or {}
        return None, f"Another SFA run is already in progress ({holder.get('owner', 'unknown')})."
    LIVE.reset(kind=kind, trigger=trigger)
    LIVE.set(lanes=lanes, steps=PREPARE_STEPS if kind == "prepare" else CONFIRM_STEPS)
    rid = LS.start_run(kind, trigger)
    LIVE.set(run_id=rid)
    return rid, None


def _finish(rid, status, msg):
    LS.finish_run(rid, status, msg, log_text())
    LIVE.set(active=False, phase="done", result={"ok": status == "done", "status": status, "message": msg, "run_id": rid})
    LIVE.log(msg, level="info" if status == "done" else "warn")
    AS.release_lock()


def run_prepare(distributor_ids=None, trigger="manual", headless=None):
    """Reads the pending loading of each distributor (all Daily ones if none given). Never confirms."""
    ds = [d for d in LS.list_distributors() if (d["id"] in distributor_ids if distributor_ids else d["frequency"] == "daily")]
    st = _settings()
    if headless is not None:
        st["headless"] = headless
    cfg = sfa_config.load_config()
    if not ds:
        msg = "No distributors to prepare (none selected / none set to Daily)."
        LIVE.reset(kind="prepare", trigger=trigger)
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "failed", "message": msg})
        return {"ok": False, "message": msg}
    rid, err = _begin("prepare", trigger, [_lane(d, PREPARE_STEPS) for d in ds])
    if err:
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "busy", "message": err})
        return {"ok": False, "status": "busy", "message": err}
    try:
        broken = [d["name"] for d in ds if d["problems"]]
        if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
            _finish(rid, "failed", "SFA login isn't set up — Settings › SFA Connection.")
            return {"ok": False, "message": "SFA login isn't set up."}
        LIVE.log(f"Preparing loadings for {len(ds)} distributor(s), up to {st['tabs']} at a time. "
                 "Chimera only reads them — nothing is confirmed without your approval.")
        for i, d in enumerate(ds):
            if d["problems"]:
                LIVE.lane(i, status="failed", message=d["problems"][0])
                LS.add_item(rid, d, "failed", message=d["problems"][0])
        runnable = [(i, d) for i, d in enumerate(ds) if not d["problems"]]
        opts = {"run_id": rid, "timeout_ms": st["timeout_ms"], "headless": st["headless"]}

        async def body(context):
            LIVE.set(phase="preparing")
            sem = asyncio.Semaphore(st["tabs"])
            await asyncio.gather(*[_prepare_lane(context, cfg, st["url"], d, i, opts, sem) for i, d in runnable])

        if runnable:
            asyncio.run(_with_browser(cfg, opts, body))
        lanes = LIVE.snapshot()["lanes"]
        ready = sum(l["status"] == "ready" for l in lanes)
        nothing = sum(l["status"] == "nothing" for l in lanes)
        failed = sum(l["status"] == "failed" for l in lanes)
        msg = (f"{ready} loading(s) waiting for your approval" + (f", {nothing} with nothing pending" if nothing else "")
               + (f", {failed} failed" if failed else "") + (f" ({', '.join(broken)} need setting up)" if broken else "") + ".")
        status = "cancelled" if LIVE.cancelled() else ("done" if ready or nothing else "failed")
        _finish(rid, status, msg)
        return {"ok": status == "done", "status": status, "message": msg, "run_id": rid, "ready": ready}
    except Exception as e:
        _finish(rid, "failed", f"Unexpected error: {e}")
        return {"ok": False, "message": str(e)}


# ---------------------------------------------------------------------------
# CONFIRM
# ---------------------------------------------------------------------------
async def _press_confirm(page, run_id, idx, name):
    """Green Confirm at the bottom, then Yes/OK on "Are you sure?" (native dialog or in-page pop-up)."""
    answered = {"native": None}

    def on_dialog(dlg):
        answered["native"] = dlg.message
        asyncio.ensure_future(dlg.accept())
    page.on("dialog", on_dialog)
    try:
        btn, _lbl, seen = await SA._find_button(page, "Confirm", 10000)
        if btn is None:
            raise StepError("confirm", "The green Confirm button wasn't found. Buttons on the page: "
                            + ("; ".join(seen[:12]) or "none"))
        await btn.scroll_into_view_if_needed()
        await btn.click()
        deadline = time.time() + 10
        said = None
        while time.time() < deadline and not answered["native"]:
            token = uuid.uuid4().hex[:8]
            said = await page.evaluate(_POPUP_YES_JS, [token])
            if said:
                await page.locator(f"[data-chimera-yes='{token}']").first.click()
                break
            await page.wait_for_timeout(300)
        if answered["native"]:
            LIVE.log(f"Answered '{answered['native'][:60]}' with OK.", lane=name)
        elif said:
            LIVE.log(f"Answered 'Are you sure?' with '{said}'.", lane=name)
        else:
            LIVE.log("No 'Are you sure?' pop-up appeared after Confirm.", lane=name, level="warn")
        await page.wait_for_timeout(2500)
    finally:
        page.remove_listener("dialog", on_dialog)


async def _confirm_lane(context, cfg, url, item, d, idx, opts, sem):
    async with sem:
        if LIVE.cancelled():
            LS.update_item(item["id"], state="pending_approval", message="Not confirmed (stopped before it started).")
            LIVE.lane(idx, status="cancelled", message="Stopped before it started — still waiting for approval.")
            return
        LIVE.lane(idx, status="running", started=time.time())
        LS.update_item(item["id"], state="confirming", confirm_run_id=opts["run_id"], decided_at=LS._now())
        page = await context.new_page()
        pressed = False
        try:
            LIVE.step(idx, "open", "active")
            await page.goto(item["confirm_url"], wait_until="domcontentloaded", timeout=opts["timeout_ms"])
            await SA._settle(page)
            if await SA._ensure_logged_in(page, cfg, opts["timeout_ms"]):
                await page.goto(item["confirm_url"], wait_until="domcontentloaded", timeout=opts["timeout_ms"])
                await SA._settle(page)
            data = await _read_confirm_page(page, opts["timeout_ms"])
            LIVE.step(idx, "open", "done")
            LIVE.step(idx, "check", "active")
            if not data or not data.get("heading") or not data.get("products"):
                raise StepError("check", "The CONFIRM LOADING page for this loading didn't open — it may already be "
                                         "confirmed or removed in SFA. Nothing was confirmed.")
            now = _loading_from_page(data)
            diffs = _differences(item, now)
            if diffs:
                msg = "Not confirmed — the loading changed in SFA since you approved it: " + "; ".join(diffs) + \
                      ". Prepare it again and re-check."
                shot = await _shot(page, opts["run_id"], f"{idx + 1:02d} {d['name']} - changed", full=True)
                LS.update_item(item["id"], state="changed", message=msg, screenshot=shot or item["screenshot"])
                LIVE.step(idx, "check", "failed")
                LIVE.lane(idx, status="changed", message=msg)
                LIVE.log(msg, lane=d["name"], level="warn")
                return
            LIVE.step(idx, "check", "done")
            if LIVE.cancelled():
                raise Cancelled()
            LIVE.step(idx, "confirm", "active")
            pressed = True
            await _press_confirm(page, opts["run_id"], idx, d["name"])
            LIVE.step(idx, "confirm", "done")
            LIVE.step(idx, "verify", "active")
            res = await _open_and_search(page, cfg, url, d, idx, opts["timeout_ms"], lane_steps=False)
            row = next((r for r in res["rows"] if r["number"] and r["number"] == item["loading_number"]), None)
            shot = await _shot(page, opts["run_id"], f"{idx + 1:02d} {d['name']} - after confirm")
            if row and not row["pending"]:
                LS.update_item(item["id"], state="confirmed", confirmed_at=LS._now(), screenshot=item["screenshot"],
                               message=f"Confirmed in SFA — View Loadings now shows '{row['confirm_cell'] or row['status']}'.")
                LIVE.step(idx, "verify", "done")
                LIVE.lane(idx, status="confirmed", message=f"{item['loading_number']} confirmed and verified.")
                LIVE.log(f"{item['loading_number']} confirmed — SFA shows it as confirmed.", lane=d["name"])
            elif row and row["pending"]:
                msg = "Pressed Confirm, but View Loadings still shows this loading as pending — check it in SFA."
                LS.update_item(item["id"], state="confirm_failed", message=msg, screenshot=shot or item["screenshot"])
                LIVE.step(idx, "verify", "failed")
                LIVE.lane(idx, status="failed", message=msg)
                LIVE.log(msg, lane=d["name"], level="error")
            else:
                msg = "Confirmed, but the loading couldn't be found again in View Loadings to double-check."
                LS.update_item(item["id"], state="confirmed", confirmed_at=LS._now(), message=msg)
                LIVE.step(idx, "verify", "skipped")
                LIVE.lane(idx, status="confirmed", message=msg)
                LIVE.log(msg, lane=d["name"], level="warn")
        except Cancelled:
            LS.update_item(item["id"], state="pending_approval" if not pressed else "confirm_failed",
                           message="Stopped before confirming — still waiting for approval." if not pressed else
                           "Stopped right after pressing Confirm — check this loading in SFA.")
            LIVE.lane(idx, status="cancelled", message="Stopped.")
        except Exception as e:
            msg = e.message if isinstance(e, StepError) else f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
            if not pressed and not isinstance(e, StepError):
                msg += " (Nothing was confirmed.)"
            shot = await _shot(page, opts["run_id"], f"{idx + 1:02d} {d['name']} - confirm failed")
            LS.update_item(item["id"], state="confirm_failed", message=msg, screenshot=shot or item["screenshot"])
            step = e.step if isinstance(e, StepError) else (LIVE.snapshot()["lanes"][idx].get("step") or "confirm")
            LIVE.step(idx, step, "failed")
            LIVE.lane(idx, status="failed", message=msg)
            LIVE.log(msg, lane=d["name"], level="error")
        finally:
            try:
                await page.close()
            except Exception:
                pass


def run_confirm(item_ids, trigger="manual", headless=None):
    """Confirms the approved loadings (items in pending_approval)."""
    items = [LS.get_item(i) for i in item_ids]
    items = [it for it in items if it and it["state"] == "pending_approval"]
    if not items:
        return {"ok": False, "message": "Nothing to confirm — those loadings aren't waiting for approval any more."}
    ds = {d["id"]: d for d in LS.list_distributors()}
    items = [it for it in items if it["distributor_id"] in ds]
    st = _settings()
    if headless is not None:
        st["headless"] = headless
    cfg = sfa_config.load_config()
    lanes = []
    for it in items:
        ln = _lane(ds[it["distributor_id"]], CONFIRM_STEPS)
        ln.update(loading_number=it["loading_number"], item_id=it["id"])
        lanes.append(ln)
    rid, err = _begin("confirm", trigger, lanes)
    if err:
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "busy", "message": err})
        return {"ok": False, "status": "busy", "message": err}
    try:
        LIVE.log(f"Confirming {len(items)} approved loading(s).")
        opts = {"run_id": rid, "timeout_ms": st["timeout_ms"], "headless": st["headless"]}

        async def body(context):
            LIVE.set(phase="confirming")
            sem = asyncio.Semaphore(st["tabs"])
            await asyncio.gather(*[_confirm_lane(context, cfg, st["url"], it, ds[it["distributor_id"]], i, opts, sem)
                                   for i, it in enumerate(items)])

        ok, err = asyncio.run(_with_browser(cfg, opts, body))
        if not ok:
            for it in items:
                LS.update_item(it["id"], state="pending_approval", message=f"Not confirmed — {err}")
        lanes = LIVE.snapshot()["lanes"]
        done = sum(l["status"] == "confirmed" for l in lanes)
        changed = sum(l["status"] == "changed" for l in lanes)
        failed = sum(l["status"] == "failed" for l in lanes)
        msg = f"{done} of {len(items)} loading(s) confirmed" + (f", {changed} changed in SFA (not confirmed)" if changed else "") \
            + (f", {failed} failed" if failed else "") + "."
        status = "cancelled" if LIVE.cancelled() else ("done" if done == len(items) else "failed" if not done else "done")
        _finish(rid, status, msg)
        return {"ok": done > 0, "status": status, "message": msg, "run_id": rid, "confirmed": done}
    except Exception as e:
        _finish(rid, "failed", f"Unexpected error: {e}")
        return {"ok": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Background, stop, log
# ---------------------------------------------------------------------------
def start_background(fn, **kw):
    if LIVE.snapshot().get("active") or SA.LIVE.snapshot().get("active"):
        return {"ok": False, "message": "Another SFA run is already in progress."}
    LIVE.reset(kind="prepare" if fn is run_prepare else "confirm", trigger=kw.get("trigger", "manual"))
    threading.Thread(target=fn, kwargs=kw, daemon=True, name="chimera-loadings").start()
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
    return was


def log_text(s=None):
    s = s or LIVE.snapshot()
    out = [f"Chimera Distributor Loadings — {s.get('kind', '')} run #{s.get('run_id') or '-'} · {s.get('trigger', '')}"]
    res = s.get("result") or {}
    if res:
        out.append(f"Result: {str(res.get('status', '')).upper()} — {res.get('message', '')}")
    out.append("")
    for l in s.get("lanes") or []:
        steps = " ".join(f"{k}:{v}" for k, v in (l.get("steps") or {}).items())
        out.append(f"[{l.get('status')}] {l.get('name')} {l.get('loading_number') or ''} — {l.get('message') or ''} {steps}")
    out.append("")
    for x in s.get("log") or []:
        out.append(f"{x['t']} {x['level'].upper():5} {('[' + x['lane'] + '] ') if x.get('lane') else ''}{x['msg']}")
    return "\n".join(out)


def detect_filters(url=None, headless=True):
    """Same as report profiles: open View Loadings and list its filters with their options."""
    return SA.detect_filters(url or _settings()["url"], headless=headless)
