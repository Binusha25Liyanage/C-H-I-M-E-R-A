"""
sequence_automation.py
Outlet Updates in SFA: visit sequence and Visit State from the reps' route
sheets (sequence_store / sequence_reader hold the data and the rules).

  Route list   Sales Organization › Route › View Route (/it/level-seven):
               Status = ACTIVE -> SEARCH -> the green Excel icon -> every
               active route (code, name, territory). Read only.

  Preview      View Outlets (/it/level-eight): Territory + Route -> SEARCH ->
               the biggest page size -> every page of the table: each
               outlet's edit link, current SEQUENCE and VISIT STATE on that
               route. Read only. The sheet then waits for approval — or, if
               approval isn't required, goes straight on to saving.

  Save         each outlet that changes: its edit page (/it/level-eight/edit/
               <id>) -> check the Customer Code is that outlet -> Outlet Visit
               State -> Customer Sequence No on the sheet's route -> phone
               fields fixed to 10 characters (SFA won't save otherwise:
               9 digits get a leading 0, +94… becomes 0…, anything else
               0000000000) -> UPDATE -> Yes -> "successfully edited".
               Dry run: everything except UPDATE.

One browser, one tab, sheets one after another (these are writes). Stop /
Emergency stop end it between outlets; Chimera closing mid-way leaves the
sheet 'approved' and the next run continues with the outlets not saved yet.
"""

import asyncio
import os
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urljoin

import automation_store as AS
import outlet_store as OS
import sequence_reader as SR
import sequence_store as SS
import sfa_automation as SA
import sfa_config

READ_STEPS = ["login", "open", "filters", "search", "read"]
SAVE_STEPS = ["login", "open", "filters", "search", "read", "save"]
STEP_LABELS = {"login": "Log in", "open": "Open", "filters": "Territory & route", "search": "Search",
               "read": "Read outlets", "save": "Save outlets", "export": "Excel"}
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "downloads", "routes")
SHOT_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "screenshots", "outlet_updates")

LIVE = SA.LiveState("outlets")
_ACTIVE = {"loop": None, "browser": None}
BLANK_PHONE = "0000000000"


class StepError(Exception):
    def __init__(self, step, message):
        super().__init__(message)
        self.step, self.message = step, message


class Cancelled(Exception):
    pass


def normalize_phone(raw):
    """'763810506' -> '0763810506'; '+94763810506' -> '0763810506'; '076 381-0506' -> '0763810506';
    empty or anything else -> '0000000000' (SFA needs exactly 10 characters)."""
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) == 11 and d.startswith("94"):
        d = "0" + d[2:]
    if len(d) == 10:
        return d
    if len(d) == 9:
        return "0" + d
    return BLANK_PHONE


def settings():
    s = AS.get_automation_settings()
    return {"routes_url": s.get("ou_routes_url") or SS.DEFAULT_ROUTES_URL,
            "outlets_url": s.get("ou_outlets_url") or OS.DEFAULT_OUTLETS_URL,
            "need_approval": s.get("seq_need_approval", "1") == "1", "dry_run": s.get("seq_dry_run", "1") == "1",
            "timeout_ms": int(s.get("page_timeout_seconds") or 60) * 1000, "headless": s.get("headless", "1") == "1",
            "paused": s.get("paused") == "1"}


# ---------------------------------------------------------------------------
# Page readers
# ---------------------------------------------------------------------------
_OUTLETS_JS = r"""
() => {
  // View Outlets: a 2-row grouped header; outlets on several routes span rows (rowspan)
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const expand = (rows) => {
    const g = [];
    rows.forEach((tr, ri) => { g[ri] = g[ri] || []; let c = 0;
      for (const cell of tr.children) {
        while (g[ri][c] !== undefined) c++;
        const cs = Math.max(1, +cell.colSpan || 1), rs = Math.max(1, +cell.rowSpan || 1);
        for (let a = 0; a < rs && ri + a < rows.length; a++) for (let b = 0; b < cs; b++) { g[ri + a] = g[ri + a] || []; g[ri + a][c + b] = cell; }
        c += cs; } });
    return g;
  };
  for (const t of [...document.querySelectorAll('table')].filter(vis)) {
    const hg = expand([...t.querySelectorAll('thead tr')]);
    if (!hg.length) continue;
    const hs = hg[hg.length - 1].map(c => norm(c && c.innerText).toLowerCase());
    const col = re => hs.findIndex(h => re.test(h));
    const iCode = col(/^outlet\s*code/), iSeq = col(/^(customer\s*)?sequence/), iRC = col(/^route\s*code/), iR = col(/^route(\s*name)?$/),
          iT = col(/^territory/), iN = col(/^outlet(\s*name)?(\s*[↓↑]+)?$/), iVS = col(/visit\s*state/);
    if (iCode < 0 || iSeq < 0) continue;
    const bg = expand([...t.querySelectorAll('tbody tr')]);
    const txt = (row, i) => i >= 0 && row[i] ? norm(row[i].innerText) : '';
    const rows = bg.map(row => {
      const tr = row.find(c => c) ? row.find(c => c).parentElement : null;
      const cells = [...new Set(row.filter(Boolean))];
      let edit = '';
      for (const c of cells) { const a = c.querySelector('a[href*="/edit/"]'); if (a) { edit = a.href; break; } }
      if (!edit) { const p = cells.map(c => c.querySelector('.fa-pencil, .pi-pencil, [class*=pencil]')).find(Boolean);
                   const a = p && p.closest('a'); if (a && a.href) edit = a.href; }
      return {code: txt(row, iCode), name: txt(row, iN), route_code: txt(row, iRC), route: txt(row, iR), territory: txt(row, iT),
              seq: txt(row, iSeq), state: txt(row, iVS), edit_id: edit};
    }).filter(r => r.code);
    const info = (document.body.innerText.match(/showing\s+([\d,]+)\s*(?:-|to)\s*([\d,]+)\s+of\s+([\d,]+)/i) || []).slice(1).map(x => +x.replace(/,/g, ''));
    const nexts = [...document.querySelectorAll('a, button, li, span')].filter(e => vis(e) && /^next$/i.test(norm(e.textContent)));
    const next = nexts[nexts.length - 1];
    let nextOk = false;
    if (next) {
      const box = next.closest('li') || next;
      nextOk = !(/disabled/i.test(box.className + ' ' + next.className) || next.disabled || box.id === 'isDisabled'
                 || box.getAttribute('aria-disabled') === 'true' || next.getAttribute('aria-disabled') === 'true'
                 || getComputedStyle(next).pointerEvents === 'none');
      if (nextOk) (next.closest('a, button') || next).setAttribute('data-chimera-next', '1');
    }
    return {table: true, rows, info, nextOk, first: rows.length ? rows[0].code + rows[0].route_code : '', hasVS: iVS >= 0};
  }
  const empty = /no (records?|data|results?)|no data available/i.test(document.body.innerText);
  return {table: false, rows: [], info: [], nextOk: false, first: '', empty};
}
"""

_SHOW_MAX_JS = r"""
() => {
  for (const sel of document.querySelectorAll('select')) {
    const opts = [...sel.options].map(o => +o.value || +o.text).filter(n => n > 0);
    if (opts.length && opts.every(n => [5, 10, 15, 20, 25, 50, 100, 200, 250, 500, 1000].includes(n))) {
      const max = Math.max(...opts);
      if (+sel.value === max) return -1;
      const o = [...sel.options].find(o => (+o.value || +o.text) === max);
      sel.value = o.value; sel.dispatchEvent(new Event('change', {bubbles: true})); return max;
    }
  }
  return 0;
}
"""

_SQUASH_PICK_JS = r"""
([value, token]) => {
  const sq = s => (s || '').replace(/\s+/g, '').toLowerCase();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const opts = [...document.querySelectorAll('%PANEL%')].filter(vis).flatMap(p => [...p.querySelectorAll('%OPTION%')]).filter(vis);
  const hit = opts.find(o => sq(o.getAttribute('aria-label')) === sq(value) || sq(o.textContent) === sq(value));
  if (!hit) return {found: false, n: opts.length, options: opts.slice(0, 30).map(o => o.textContent.replace(/\s+/g, ' ').trim())};
  hit.setAttribute('data-chimera-opt', token); hit.scrollIntoView({block: 'center'});
  return {found: true};
}
""".replace("%PANEL%", SA.PANEL_SEL).replace("%OPTION%", SA.OPTION_SEL)

_EDIT_JS = r"""
([route, token]) => {
  // Edit Outlet: the Customer Code (first read-only input) and the Route List (route name + Customer Sequence No)
  const sq = s => (s || '').replace(/\s+/g, '').toLowerCase();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const ro = [...document.querySelectorAll('input[readonly], input[disabled]')].filter(vis).find(i => i.value && i.value.trim());
  const nums = [...document.querySelectorAll('input[type=number]')].filter(vis);
  const routes = [];
  let hit = null;
  for (const inp of nums) {
    let a = inp, name = '';
    for (let k = 0; k < 6 && a && !name; k++) { a = a.parentElement; const li = a && a.querySelector('li'); if (li) name = li.textContent.replace(/\s+/g, ' ').trim(); }
    routes.push(name);
    if (!hit && name && sq(name) === sq(route)) hit = inp;
  }
  if (!hit && nums.length === 1 && routes.length === 1 && !routes[0]) hit = nums[0];
  if (hit) hit.setAttribute('data-chimera-seq', token);
  return {code: ro ? ro.value.trim() : '', routes: routes.filter(Boolean), found: !!hit, seq: hit ? hit.value.trim() : ''};
}
"""

_ERRORS_JS = r"""
() => [...document.querySelectorAll('small, .text-danger, .invalid-feedback, .error, span, p')]
  .filter(e => e.children.length === 0 && (e.offsetWidth || e.offsetHeight))
  .filter(e => { const m = getComputedStyle(e).color.match(/rgb\((\d+), (\d+), (\d+)/); return m && +m[1] > 150 && +m[2] < 100 && +m[3] < 100; })
  .map(e => e.textContent.trim()).filter(t => t.length > 2 && t !== '*').slice(0, 6)
"""

_DIALOG_JS = r"""
() => { const el = [...document.querySelectorAll('.swal2-popup, .swal-modal, .modal.show, [role=dialog], [role=alertdialog]')]
          .find(e => e.offsetParent !== null || getComputedStyle(e).position === 'fixed');
        return el ? el.innerText.replace(/\s+/g, ' ').trim() : ''; }
"""

PHONE_FIELDS = [("Telephone", re.compile(r"^\s*Telephone\s*\*?\s*$", re.I), False),
                ("Mobile", re.compile(r"^\s*Mobile\s*\*?\s*$", re.I), False),
                ("Contact Person Number", re.compile(r"^\s*Contact\s+Person\s+Number\s*\*?\s*$", re.I), False),
                ("Contact Person", re.compile(r"^\s*Contact\s+Person\s*\*?\s*$", re.I), True)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check():
    if LIVE.cancelled():
        raise Cancelled()


async def _open(page, cfg, url, timeout_ms):
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await SA._settle(page)
    if await SA._ensure_logged_in(page, cfg, timeout_ms):
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await SA._settle(page)


async def _wait_field(page, label, timeout_ms):
    deadline = time.time() + timeout_ms / 1000
    while (await SA._find_field(page, label))[0] is None:
        if time.time() > deadline:
            return False
        _check()
        await page.wait_for_timeout(400)
    return True


async def _choose(page, label, value, wait_s=15):
    """Picks a dropdown value. SFA's filter box treats every space as a character, so if the normal pick fails,
    only the first word is typed and names are compared with all spaces removed. Route options load after a
    territory is picked, so it keeps trying for a while."""
    deadline = time.time() + wait_s
    last = ""
    while True:
        _check()
        try:
            await SA.fill_filter(page, {"label": label, "type": "dropdown", "value": value}, datetime.now().date().isoformat())
            return
        except SA.StepError as e:
            last = e.message
        ctrl, kind = await SA._find_field(page, label)
        if ctrl is not None and kind in ("dropdown", "ngselect"):
            try:
                await SA._open_panel(page, ctrl, kind)
                await SA._type_in_panel_filter(page, ctrl, value.split()[0] if value.split() else value)
                token = uuid.uuid4().hex[:10]
                res = await page.evaluate(_SQUASH_PICK_JS, [value, token])
                if res.get("found"):
                    await page.locator(f"[data-chimera-opt='{token}']").first.click()
                    await page.wait_for_timeout(500)
                    fresh, _k = await SA._find_field(page, label)
                    if fresh is not None and SS.squash(value) in SS.squash(await SA._control_text(fresh)):
                        return
                await SA._close_panel(page)
            except Exception:
                pass
        if time.time() > deadline:
            raise StepError("filters", f"{label} '{value}' couldn't be picked on View Outlets. {last}")
        await page.wait_for_timeout(1200)


async def _shot(page, name):
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        p = os.path.join(SHOT_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_{re.sub(r'[^A-Za-z0-9]+', '_', name)[:60]}.png")
        await page.screenshot(path=p, full_page=True)
        return p
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Reading a route's outlets
# ---------------------------------------------------------------------------
async def read_route(page, cfg, sheet, idx, opts):
    url = opts["outlets_url"]
    LIVE.step(idx, "open", "active")
    await _open(page, cfg, url, opts["timeout_ms"])
    if not await _wait_field(page, "Route", opts["timeout_ms"]):
        raise StepError("open", "View Outlets opened but its Territory / Route filters never appeared — check its "
                                "address in Outlet Updates › Settings.")
    LIVE.step(idx, "open", "done")
    LIVE.step(idx, "filters", "active")
    await _choose(page, "Territory", sheet["territory"])
    await page.wait_for_timeout(600)
    await _choose(page, "Route", sheet["route_name"], wait_s=25)
    LIVE.step(idx, "filters", "done")
    LIVE.step(idx, "search", "active")
    btn, _l, seen = await SA._find_button(page, "SEARCH", 10000)
    if btn is None:
        raise StepError("search", "No SEARCH button on View Outlets. Buttons seen: " + ("; ".join(seen[:10]) or "none"))
    await btn.click()
    await SA._settle(page, 15000)
    res, deadline = None, time.time() + max(30, opts["timeout_ms"] / 1000)
    while time.time() < deadline:
        _check()
        await page.wait_for_timeout(700)
        try:
            res = await page.evaluate(_OUTLETS_JS)
        except Exception:
            continue
        if res["table"] and res["rows"] or res.get("empty"):
            break
    if not res or not res["table"] or not res["rows"]:
        if res and res.get("empty"):
            raise StepError("search", f"View Outlets lists no outlets for {sheet['route_name']} ({sheet['territory']}).")
        raise StepError("search", "No outlet table appeared after SEARCH on View Outlets.")
    LIVE.step(idx, "search", "done")
    LIVE.step(idx, "read", "active")
    if await page.evaluate(_SHOW_MAX_JS) > 0:
        await SA._settle(page, 15000)
        for _w in range(30):
            await page.wait_for_timeout(500)
            try:
                res = await page.evaluate(_OUTLETS_JS)
            except Exception:
                continue
            if res["table"] and res["rows"]:
                break
    found, pages = {}, 0
    while True:
        _check()
        pages += 1
        for r in res["rows"]:
            key = (r["code"].upper(), r["route_code"].upper() or SS.squash(r["route"]))
            found.setdefault(key, r)
        if not res["nextOk"] or pages > 200:
            break
        before = res["first"]
        await page.locator("[data-chimera-next='1']").last.click()
        for _w in range(40):
            await page.wait_for_timeout(300)
            try:
                res = await page.evaluate(_OUTLETS_JS)
            except Exception:
                continue
            if res["first"] != before:
                break
        else:
            break
    rows = list(found.values())
    code, rname = sheet["route_code"].upper(), SS.squash(sheet["route_name"])
    mine = [r for r in rows if (r["route_code"].upper() == code if r["route_code"] else SS.squash(r["route"]) == rname)]
    if rows and not mine:
        other = sorted({r["route_code"] or r["route"] for r in rows})[:4]
        raise StepError("read", f"SFA showed outlets of another route ({', '.join(other)}) instead of "
                                f"{sheet['route_code']} — the Route filter didn't take. Nothing was used.")
    total = res["info"][2] if len(res.get("info") or []) == 3 else None
    LIVE.step(idx, "read", "done")
    LIVE.log(f"{len(mine)} outlet(s) on the route in SFA" + (f" (SFA's table says {total} rows in all)" if total else "")
             + ("" if res.get("hasVS", True) else " — no VISIT STATE column, Visit States read on each outlet's page"),
             lane=sheet["route_name"])
    return [{"code": r["code"], "name": r["name"], "edit_id": r["edit_id"], "seq": r["seq"],
             "state": (r["state"] or "").title()} for r in mine]


# ---------------------------------------------------------------------------
# Saving one outlet
# ---------------------------------------------------------------------------
async def _fix_phones(page, dry_run):
    changes = []
    for name, rx, only_numeric in PHONE_FIELDS:
        lab = page.locator("label").filter(has_text=rx).first
        if not await lab.count():
            continue
        inp = lab.locator("xpath=following::input[1]")
        if not await inp.count():
            continue
        cur = await inp.input_value()
        t = cur.strip()
        if only_numeric and not re.match(r"^[\d\s+\-()]+$", t):
            continue
        fixed = normalize_phone(t)
        if fixed == cur:
            continue
        changes.append(f'{name}: "{t}" → "{fixed}"')
        if dry_run:
            continue
        await inp.click()
        await inp.fill("")
        await inp.fill(fixed)
        await inp.press("Tab")
        if (await inp.input_value()) != fixed:
            raise StepError("save", f"Couldn't set {name} to {fixed}.")
    return changes


async def save_outlet(page, cfg, line, route_name, dry_run, timeout_ms):
    """Returns {old_seq, old_state, phone_fixes, saved}. Raises StepError with the reason."""
    url = line["edit_id"]
    if not url:
        raise StepError("save", "No edit link for this outlet on View Outlets.")
    await _open(page, cfg, url, timeout_ms)
    token = uuid.uuid4().hex[:10]
    info, deadline = None, time.time() + 30
    while time.time() < deadline:
        _check()
        try:
            info = await page.evaluate(_EDIT_JS, [route_name, token])
        except Exception:
            info = None
        if info and info["code"] and (info["found"] or info["routes"]):
            break
        await page.wait_for_timeout(500)
    if not info or not info["code"]:
        raise StepError("save", "The outlet's edit page didn't load (no Customer Code).")
    if info["code"].upper() != line["outlet_code"].upper():
        raise StepError("save", f"The edit page shows {info['code']}, not {line['outlet_code']} — skipped for safety.")
    if not info["found"]:
        raise StepError("save", f"This outlet isn't on route '{route_name}' on its edit page (routes: "
                                f"{', '.join(info['routes']) or 'none'}).")
    vs_ctrl, _k = await SA._find_field(page, "Outlet Visit State")
    shown = (await SA._control_text(vs_ctrl)) if vs_ctrl is not None else ""
    old_state = next((v for v in SS.VISIT_STATES if v.lower() in shown.lower()), shown.strip().title())
    want_state = line["new_state"] or old_state
    if want_state and vs_ctrl is None:
        raise StepError("save", "No 'Outlet Visit State' field on the edit page.")
    need_state = bool(want_state) and SS.squash(want_state) != SS.squash(old_state)
    need_seq = str(line["new_seq"]) != info["seq"]
    res = {"old_seq": info["seq"], "old_state": old_state, "phone_fixes": [], "saved": False,
           "already": not need_state and not need_seq}
    if res["already"]:
        return res                    # SFA already matches (changed by someone since the preview)
    fixes = await _fix_phones(page, dry_run)
    res["phone_fixes"] = fixes
    if dry_run:
        return res
    if need_state:
        try:
            await SA.fill_filter(page, {"label": "Outlet Visit State", "type": "dropdown", "value": want_state}, "")
        except SA.StepError as e:
            raise StepError("save", f"Visit State: {e.message}")
    seq = page.locator(f"[data-chimera-seq='{token}']").first
    if need_seq:
        await seq.click()
        await seq.fill(str(line["new_seq"]))
        await seq.press("Tab")
        if (await seq.input_value()).strip() != str(line["new_seq"]):
            raise StepError("save", "Couldn't type the new sequence into the form.")
    for attempt in (1, 2):
        btn, _l, seen = await SA._find_button(page, "UPDATE", 8000)
        if btn is None:
            raise StepError("save", "No UPDATE button on the edit page.")
        await btn.click()
        yes = page.get_by_role("button", name=re.compile(r"^\s*yes\s*$", re.I))
        try:
            await yes.first.wait_for(state="visible", timeout=8000)
            break
        except Exception:
            errs = await page.evaluate(_ERRORS_JS)
            if attempt == 2:
                raise StepError("save", "SFA didn't accept the form: " + ("; ".join(errs) or "no confirmation appeared."))
            tel = page.locator("label").filter(has_text=PHONE_FIELDS[0][1]).first.locator("xpath=following::input[1]")
            if await tel.count():                     # re-enter Telephone so the form validates again
                v = await tel.input_value()
                await tel.fill("")
                await tel.fill(v)
                await tel.press("Tab")
    await yes.first.click()
    ok = page.get_by_text(re.compile(r"successfully\s+edited", re.I))
    try:
        await ok.first.wait_for(state="visible", timeout=30000)
    except Exception:
        msg = await page.evaluate(_DIALOG_JS)
        raise StepError("save", "SFA didn't confirm the update. " + (msg or ""))
    okb = page.get_by_role("button", name=re.compile(r"^\s*ok\s*$", re.I))
    if await okb.count():
        try:
            await okb.first.click(timeout=3000)
        except Exception:
            pass
    res["saved"] = True
    return res


# ---------------------------------------------------------------------------
# A run: read previews for new sheets, save approved ones
# ---------------------------------------------------------------------------
async def _with_browser(cfg, opts, body):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await SA._launch(p, cfg.get("browser", "chrome"), opts["headless"])
        _ACTIVE.update(loop=asyncio.get_running_loop(), browser=browser)
        try:
            ctx = await browser.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
            page = await ctx.new_page()
            LIVE.set(phase="logging_in")
            try:
                _ok, msg = await SA._login(page, cfg, opts["timeout_ms"])
            except Exception as e:
                raise StepError("login", e.message if isinstance(e, SA.StepError) else
                                f"Couldn't reach SFA: {str(e).splitlines()[0]}")
            LIVE.log(msg)
            return await body(page)
        finally:
            _ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:
                pass


def _lane(name, steps):
    return {"name": name, "status": "queued", "message": "", "steps": {s: "pending" for s in steps}, "elapsed": 0,
            "started": None}


def run(sheet_ids=None, trigger="manual", headless=None):
    """Reads SFA for sheets just received (-> preview) and saves approved ones. sheet_ids limits it to those."""
    st = settings()
    if headless is not None:
        st["headless"] = headless
    cfg = sfa_config.load_config()
    pick = lambda states: [s for s in SS.list_sheets(states) if not sheet_ids or s["id"] in sheet_ids]
    todo = sorted(pick(("received", "approved")), key=lambda s: s["id"])
    if not todo:
        return {"ok": True, "status": "nothing", "message": "No sheet is waiting for SFA."}
    if not AS.acquire_lock(f"outlet updates ({datetime.now():%H:%M})"):
        holder = AS.lock_holder() or {}
        msg = f"Another SFA run is already in progress ({holder.get('owner', 'unknown')})."
        LIVE.reset(kind="sequence", trigger=trigger)
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "busy", "message": msg})
        return {"ok": False, "status": "busy", "message": msg}
    LIVE.reset(kind="sequence", trigger=trigger, steps=SAVE_STEPS, dry_run=st["dry_run"],
               lanes=[_lane(f"{s['route_name']} ({s['territory']})",
                            READ_STEPS if s["state"] == "received" and st["need_approval"] else SAVE_STEPS)
                      for s in todo])
    rid = SS.start_run("apply" if any(s["state"] == "approved" for s in todo) else "read", trigger)
    LIVE.set(run_id=rid, sheets=[s["id"] for s in todo])
    if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
        return _finish(rid, "failed", "SFA login isn't set up — Settings › SFA Connection.")
    LIVE.log(("DRY RUN — every outlet is opened and checked, but UPDATE is never pressed. " if st["dry_run"] else "")
             + f"{len(todo)} route sheet(s): " + ", ".join(f"#{s['id']} {s['route_name']}" for s in todo))
    opts = {"timeout_ms": st["timeout_ms"], "outlets_url": st["outlets_url"]}
    totals = {"read": 0, "saved": 0, "would": 0, "failed": 0, "sheets_failed": 0}

    async def body(page):
        LIVE.set(phase="working")
        for idx, s in enumerate(todo):
            if LIVE.cancelled():
                break
            LIVE.step(idx, "login", "done")
            LIVE.lane(idx, status="running", started=time.time())
            lane = s["route_name"]
            try:
                if s["state"] == "received":
                    rows = await read_route(page, cfg, s, idx, opts)
                    sheet = SS.build_preview(s["id"], rows)
                    totals["read"] += 1
                    errs = [p["text"] for p in sheet["problems"] if p["level"] == "error"]
                    if errs:
                        SS.set_sheet(s["id"], state="failed", message=errs[0])
                        LIVE.lane(idx, status="failed", message=errs[0])
                        continue
                    sm = sheet["summary"]
                    msg = (f"{sm['changes']} outlet(s) to change: {sm['seq_changes']} sequence, {sm['state_changes']} "
                           f"Visit State ({sm['top']} Top 15).")
                    SS.set_sheet(s["id"], state="ready", message="Waiting for your approval — " + msg)
                    if st["need_approval"]:
                        LIVE.lane(idx, status="done", message="Preview ready — waiting for your approval. " + msg)
                        LIVE.log("Preview ready: " + msg, lane=lane)
                        continue
                    SS.approve(s["id"], by="auto")
                    LIVE.log("Approval isn't required — saving straight away. " + msg, lane=lane)
                else:
                    for k in ("open", "filters", "search", "read"):
                        LIVE.step(idx, k, "skipped")
                await _apply(page, cfg, s["id"], idx, st, opts, totals)
            except Cancelled:
                LIVE.lane(idx, status="cancelled", message="Stopped.")
                cur = SS.get_sheet(s["id"], lines=False)
                if cur["state"] == "applying":
                    SS.set_sheet(s["id"], state="approved", message="Stopped part-way — the outlets not saved yet "
                                                                     "are saved next time it runs.")
                break
            except StepError as e:
                totals["sheets_failed"] += 1
                LIVE.step(idx, e.step, "failed")
                LIVE.lane(idx, status="failed", message=e.message)
                LIVE.log(e.message, lane=lane, level="error")
                cur = SS.get_sheet(s["id"], lines=False)
                if cur["state"] == "applying":
                    SS.set_sheet(s["id"], state="approved", message=f"Stopped by an error: {e.message} — run it again "
                                                                     "to continue.")
                elif cur["state"] == "received":
                    SS.set_sheet(s["id"], message=f"Couldn't read SFA: {e.message}")
                await _shot(page, f"seq_{s['id']}_{e.step}")

    try:
        asyncio.run(_with_browser(cfg, opts | {"headless": st["headless"]}, body))
    except StepError as e:
        return _finish(rid, "failed", e.message)
    except Exception as e:
        if LIVE.cancelled():
            return _finish(rid, "cancelled", "Stopped by you.")
        return _finish(rid, "failed", f"Unexpected error: {type(e).__name__}: {e}")
    parts = []
    if totals["read"]:
        parts.append(f"{totals['read']} preview(s) read")
    if totals["saved"]:
        parts.append(f"{totals['saved']} outlet(s) saved in SFA")
    if totals["would"]:
        parts.append(f"dry run: {totals['would']} outlet(s) would be saved")
    if totals["failed"]:
        parts.append(f"{totals['failed']} outlet(s) failed")
    if totals["sheets_failed"]:
        parts.append(f"{totals['sheets_failed']} sheet(s) stopped by an error")
    status = "cancelled" if LIVE.cancelled() else ("failed" if totals["sheets_failed"] and not
                                                   (totals["read"] or totals["saved"] or totals["would"]) else "done")
    return _finish(rid, status, ("; ".join(parts) or "Nothing to do") + ".")


async def _apply(page, cfg, sid, idx, st, opts, totals):
    s = SS.get_sheet(sid, lines=False)
    lane = s["route_name"]
    dry = st["dry_run"]
    todo = SS.changes(sid)
    SS.set_sheet(sid, state="applying", message="")
    LIVE.step(idx, "save", "active")
    LIVE.log(f"{'Checking' if dry else 'Saving'} {len(todo)} outlet(s)…", lane=lane)
    done = 0
    for line in todo:
        _check()
        try:
            r = await save_outlet(page, cfg, line, s["route_name"], dry, opts["timeout_ms"])
            if r["already"]:
                SS.set_line(line["id"], outcome="unchanged", message="SFA already had these values — nothing saved.")
                LIVE.log(f"{line['outlet_code']}: already matches in SFA.", lane=lane)
                done += 1
                continue
            fixes = "; ".join(r["phone_fixes"])
            what = []
            if str(line["new_seq"]) != (r["old_seq"] or line["cur_seq"]):
                what.append(f"sequence {r['old_seq'] or '-'} → {line['new_seq']}")
            if line["new_state"] and SS.squash(line["new_state"]) != SS.squash(r["old_state"] or line["cur_state"]):
                what.append(f"{r['old_state'] or '-'} → {line['new_state']}")
            msg = ", ".join(what) or "no change needed"
            if fixes:
                msg += f" · phones {'to fix' if dry else 'fixed'}: {fixes}"
            if dry:
                SS.set_line(line["id"], outcome="would_update", message=msg, phone_fixes=fixes)
                totals["would"] += 1
            else:
                SS.set_line(line["id"], outcome="updated", message=msg, phone_fixes=fixes,
                            done_at=datetime.now().isoformat(timespec="seconds"))
                totals["saved"] += 1
            LIVE.log(f"{line['outlet_code']}: {msg}{' (dry run — not saved)' if dry else ' — saved'}.", lane=lane,
                     level="info")
        except StepError as e:
            SS.set_line(line["id"], outcome="failed", message=e.message)
            totals["failed"] += 1
            LIVE.log(f"{line['outlet_code']}: {e.message}", lane=lane, level="error")
            await _shot(page, f"seq_{sid}_{line['outlet_code']}")
        except Cancelled:
            raise
        except Exception as e:
            SS.set_line(line["id"], outcome="failed", message=f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
            totals["failed"] += 1
            LIVE.log(f"{line['outlet_code']}: unexpected error {e}", lane=lane, level="error")
        done += 1
        LIVE.lane(idx, message=f"{done}/{len(todo)} outlet(s) {'checked' if dry else 'done'}")
    LIVE.step(idx, "save", "done")
    state, msg = SS.finish_sheet(sid, dry, LIVE.snapshot().get("run_id"))
    LIVE.lane(idx, status="done" if state in ("done", "ready") else ("failed" if state == "failed" else "done"),
              message=msg)
    LIVE.log(msg, lane=lane, level="info" if state in ("done", "ready") else "warn")


def _finish(rid, status, msg):
    SS.finish_run(rid, status, msg, log_text())
    LIVE.set(active=False, phase="done", result={"ok": status == "done", "status": status, "message": msg, "run_id": rid})
    LIVE.log(msg, level="info" if status == "done" else "warn")
    AS.release_lock()
    return {"ok": status == "done", "status": status, "message": msg, "run_id": rid}


# ---------------------------------------------------------------------------
# SFA's active route list (View Route)
# ---------------------------------------------------------------------------
async def _download_routes(page, cfg, url, timeout_ms):
    LIVE.step(0, "open", "active")
    await _open(page, cfg, url, timeout_ms)
    if not await _wait_field(page, "Status", timeout_ms):
        raise StepError("open", "View Route opened but its Status filter never appeared — check its address in "
                                "Outlet Updates › Settings.")
    LIVE.step(0, "open", "done")
    LIVE.step(0, "filters", "active")
    try:
        await SA.fill_filter(page, {"label": "Status", "type": "dropdown", "value": "ACTIVE"}, "")
    except SA.StepError as e:
        raise StepError("filters", e.message)
    LIVE.step(0, "filters", "done")
    LIVE.step(0, "search", "active")
    btn, _l, seen = await SA._find_button(page, "SEARCH", 10000)
    if btn is None:
        raise StepError("search", "No SEARCH button on View Route.")
    await btn.click()
    await SA._settle(page, 20000)
    await page.wait_for_timeout(1500)
    LIVE.step(0, "search", "done")
    LIVE.step(0, "export", "active")
    el, _m, seen = await SA._find_button(page, "Excel", timeout_ms, export_mode=True)
    if el is None:
        raise StepError("export", "No Excel icon appeared after SEARCH on View Route. Seen: " + ("; ".join(seen[:15]) or "none"))
    dl = await SA._click_and_download(page, el, max(timeout_ms, 120000))
    if dl is None:
        raise StepError("export", "Clicked the Excel icon but no file downloaded.")
    fail = await dl.failure()
    if fail:
        raise StepError("export", f"Download failed: {fail}")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    path = os.path.join(DOWNLOAD_DIR, f"routes_active_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    await dl.save_as(path)
    LIVE.step(0, "export", "done")
    return path


def run_routes(trigger="manual", headless=None):
    st = settings()
    if headless is not None:
        st["headless"] = headless
    cfg = sfa_config.load_config()
    steps = ["login", "open", "filters", "search", "export", "read"]
    if not AS.acquire_lock(f"route list ({datetime.now():%H:%M})"):
        holder = AS.lock_holder() or {}
        msg = f"Another SFA run is already in progress ({holder.get('owner', 'unknown')})."
        LIVE.reset(kind="routes", trigger=trigger)
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "busy", "message": msg})
        return {"ok": False, "status": "busy", "message": msg}
    LIVE.reset(kind="routes", trigger=trigger, steps=steps, lanes=[_lane("Active routes", steps)])
    rid = SS.start_run("routes", trigger)
    LIVE.set(run_id=rid)
    if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
        return _finish(rid, "failed", "SFA login isn't set up — Settings › SFA Connection.")
    LIVE.log(f"Reading SFA's active routes from View Route ({st['routes_url']}). Nothing is changed in SFA.")
    LIVE.lane(0, status="running", started=time.time())

    async def body(page):
        LIVE.step(0, "login", "done")
        return await _download_routes(page, cfg, st["routes_url"], st["timeout_ms"])

    try:
        path = asyncio.run(_with_browser(cfg, {"timeout_ms": st["timeout_ms"], "headless": st["headless"]}, body))
        LIVE.step(0, "read", "active")
        rows = SR.read_routes_excel(path)
        if not rows:
            raise StepError("read", "The View Route export lists no active routes — the route list wasn't changed.")
        res = SS.apply_routes(rows, complete=True)
        LIVE.step(0, "read", "done")
        msg = "Route list updated: " + SS.routes_summary_text(res)
        LIVE.lane(0, status="done", message=msg)
        return _finish(rid, "done", msg)
    except StepError as e:
        LIVE.step(0, e.step, "failed")
        LIVE.lane(0, status="failed", message=e.message)
        return _finish(rid, "failed", e.message)
    except SR.ReadError as e:
        LIVE.lane(0, status="failed", message=str(e))
        return _finish(rid, "failed", str(e))
    except Exception as e:
        if LIVE.cancelled():
            return _finish(rid, "cancelled", "Stopped by you.")
        return _finish(rid, "failed", f"Unexpected error: {type(e).__name__}: {e}")


def import_routes_excel(path):
    rows = SR.read_routes_excel(path)
    if not rows:
        return {"ok": False, "message": "No active routes in that file."}
    rid = SS.start_run("routes", "file")
    res = SS.apply_routes(rows, complete=False)
    msg = "From the Excel file: " + SS.routes_summary_text(res)
    SS.finish_run(rid, "done", msg, "")
    return {"ok": True, "message": msg, "result": res}


# ---------------------------------------------------------------------------
# Adding sheets (upload now; WhatsApp and Google Sheets use the same path)
# ---------------------------------------------------------------------------
def add_files(paths, source="upload", sender=""):
    """Reads route sheet files -> new sheets. A file already received (same content) isn't added twice."""
    out = []
    for p in paths:
        name = os.path.basename(p)
        try:
            h = SS.file_hash(p)
        except OSError as e:
            out.append({"file": name, "ok": False, "message": f"Can't open the file: {e}"})
            continue
        dup = SS.find_by_hash(h)
        if dup:
            out.append({"file": name, "ok": False, "duplicate": dup,
                        "message": f"Already received (sheet #{dup}) — not added again."})
            continue
        try:
            parsed = SR.read_workbook(p)
        except SR.ReadError as e:
            sid = SS.add_sheet({"problems": [{"level": "error", "text": str(e)}], "lines": []}, source, name, p, h, sender)
            out.append({"file": name, "ok": False, "sheet": sid, "message": str(e)})
            continue
        for k, one in enumerate(parsed):
            sid = SS.add_sheet(one, source, name + (f" › {one['tab']}" if len(parsed) > 1 else ""), p,
                               h if k == 0 else f"{h}:{k}", sender)
            s = SS.get_sheet(sid, lines=False)
            out.append({"file": name, "tab": one["tab"], "ok": s["state"] == "received", "sheet": sid,
                        "route": f"{s['route_name']} ({s['route_code']})" if s["route_code"] else "",
                        "message": s["message"] or f"{len(one['lines'])} outlets read."})
    return out


def start_background(target="run", **kw):
    import itinerary_automation, loading_automation
    if LIVE.snapshot().get("active") or SA.LIVE.snapshot().get("active") or \
            loading_automation.LIVE.snapshot().get("active") or itinerary_automation.LIVE.snapshot().get("active"):
        return {"ok": False, "message": "Another SFA run is already in progress."}
    fn = run_routes if target == "routes" else run
    LIVE.reset(kind="routes" if target == "routes" else "sequence", trigger=kw.get("trigger", "manual"))
    threading.Thread(target=fn, kwargs=kw, daemon=True, name=f"chimera-seq-{target}").start()
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
    kind = {"routes": "route list update", "sequence": "rep sheets"}.get(s.get("kind"), s.get("kind", ""))
    out = [f"Chimera Outlet Updates — {kind} #{s.get('run_id') or '-'} · {s.get('trigger', '')}"
           + (" · DRY RUN" if s.get("dry_run") else "")]
    res = s.get("result") or {}
    if res:
        out.append(f"Result: {str(res.get('status', '')).upper()} — {res.get('message', '')}")
    out.append("")
    for l in s.get("lanes") or []:
        steps = " ".join(f"{k}:{v}" for k, v in (l.get("steps") or {}).items())
        out.append(f"[{l.get('status')}] {l.get('name')} — {l.get('message') or ''} {steps}")
    out.append("")
    for x in s.get("log") or []:
        out.append(f"{x['t']} {x['level'].upper():5} {('[' + x['lane'] + '] ') if x.get('lane') else ''}{x['msg']}")
    return "\n".join(out)
