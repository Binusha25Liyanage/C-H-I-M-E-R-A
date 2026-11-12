"""
loading_create.py
Adds a new loading in SFA for one distributor (ADD LOADING page,
default https://lakmee.salespro.lk/it/loading/create).

Two steps, so nothing is saved until the user has seen the numbers:

  read_form(id)   opens the page, fills Loading For / Product Company /
                  Territory / Distributor / Sales Rep and reads the product
                  table (available stock, last unloaded qty, price). If SFA
                  answers with an error pop-up (e.g. "Previously loaded
                  quantities are not unloaded in the selected sales rep")
                  that message is returned instead. Nothing is saved.
  create(id, qty) opens the page again (fresh numbers), checks every chosen
                  quantity is still within the available stock, types the
                  quantities and presses SAVE (+ "Are you sure?" Yes) — or,
                  in dry-run mode, stops before SAVE and keeps a screenshot.
                  After saving it checks View Loadings shows the new loading
                  as pending; it is then confirmed through Approve Loadings.

Fields come from the distributor (its SFA territory and name), can be
changed per distributor (add_fields), and the Sales Rep is picked
automatically when SFA offers only one.
"""

import asyncio
import json
import os
import re
import time
import uuid
from datetime import date, datetime

import automation_store as AS
import loading_automation as LA
import loading_store as LS
import sfa_automation as SA
import sfa_config

DEFAULT_URL = "https://lakmee.salespro.lk/it/loading/create"
SHOT_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "screenshots", "add_loading")


class AddError(Exception):
    def __init__(self, message, step=""):
        super().__init__(message)
        self.message, self.step = message, step


_DIALOG_JS = r"""
() => {
  // A visible pop-up (SweetAlert / modal / dialog): its title + text and its buttons
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const sel = '.swal2-popup, .swal-modal, .sweet-alert, [role=dialog], [role=alertdialog], .modal.show .modal-content, .p-dialog, .mat-dialog-container';
  const box = [...document.querySelectorAll(sel)].filter(vis).pop();
  if (!box) return null;
  const title = norm((box.querySelector('.swal2-title, .swal-title, h2, .modal-title, .p-dialog-title') || {}).innerText);
  const text = norm((box.querySelector('.swal2-html-container, .swal2-content, .swal-text, p, .modal-body, .p-dialog-content') || {}).innerText);
  const buttons = [...box.querySelectorAll('button, .btn, [role=button]')].filter(vis).map(b => norm(b.innerText));
  return {title, text, all: norm(box.innerText), buttons};
}
"""

_CLICK_DIALOG_JS = r"""
([want]) => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const sel = '.swal2-popup, .swal-modal, .sweet-alert, [role=dialog], [role=alertdialog], .modal.show .modal-content, .p-dialog, .mat-dialog-container';
  const box = [...document.querySelectorAll(sel)].filter(vis).pop();
  if (!box) return false;
  const bs = [...box.querySelectorAll('button, .btn, [role=button]')].filter(vis);
  const b = bs.find(x => want.includes(norm(x.innerText))) || null;
  if (!b) return false;
  b.click(); return norm(b.innerText);
}
"""

_TABLE_JS = r"""
([token]) => {
  // The product table: PRODUCT | PRODUCT CODE | PRICE | AVAILABLE STOCK | LAST UNLOADED QTY | QUANTITY | VOLUME | AMOUNT
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const valOf = c => { if (!c) return ''; const i = c.querySelector('input, textarea, select'); return norm(i ? i.value : c.innerText); };
  const field = (re) => {
    for (const l of [...document.querySelectorAll('label, span, div, p, th')].filter(vis)) {
      if (l.children.length > 1 || !re.test(norm(l.childNodes.length ? [...l.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ') : l.textContent))) continue;
      let box = l.parentElement;
      for (let up = 0; up < 3 && box; up++, box = box.parentElement) {
        const inp = [...box.querySelectorAll('input')].find(i => vis(i) && (l.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_FOLLOWING || l.contains(i)));
        if (inp) return norm(inp.value);
      }
    }
    return '';
  };
  for (const t of [...document.querySelectorAll('table')].filter(vis)) {
    const heads = [...t.querySelectorAll('thead th, thead td')];
    const hs = (heads.length ? heads : [...(t.querySelector('tr') || {children: []}).children]).map(h => norm(h.innerText).toLowerCase());
    const col = re => hs.findIndex(h => re.test(h));
    const iP = col(/^product$/), iC = col(/product\s*code|^code/), iPr = col(/^price/), iS = col(/available\s*stock|stock/),
          iU = col(/last\s*unloaded/), iQ = col(/^quantity|^qty/), iA = col(/^amount/);
    if (iS < 0 || iQ < 0) continue;
    const rows = [];
    [...t.querySelectorAll('tbody tr')].forEach((r, k) => {
      const c = r.children; if (c.length <= Math.max(iS, iQ)) return;
      const q = c[iQ].querySelector('input');
      const name = valOf(c[iP >= 0 ? iP : 0]);
      if (!q || /^total$/i.test(name)) return;
      q.setAttribute('data-chimera-q', token + '-' + rows.length);
      rows.push({name, code: iC >= 0 ? valOf(c[iC]) : '', price: iPr >= 0 ? valOf(c[iPr]) : '', stock: valOf(c[iS]),
                 last_unloaded: iU >= 0 ? valOf(c[iU]) : '', qty: q.value, amount: iA >= 0 ? valOf(c[iA]) : '',
                 qref: token + '-' + (rows.length)});
    });
    return {rows, number: field(/^loading\s*(number|no)\.?$/i)};
  }
  return {rows: [], number: field(/^loading\s*(number|no)\.?$/i)};
}
"""


def _n(v):
    try:
        return float(str(v or "0").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def settings():
    s = AS.get_automation_settings()
    return {"url": s.get("add_loading_url") or DEFAULT_URL, "loading_for": s.get("add_loading_for", "Today"),
            "company": s.get("add_loading_company", ""), "dry_run": s.get("add_loading_dry_run", "1") == "1",
            "timeout_ms": int(s.get("page_timeout_seconds") or 60) * 1000,
            "headless": s.get("headless", "1") == "1"}


def fields_for(d):
    """Territory / Distributor / Sales Rep for this distributor's ADD LOADING form."""
    extra = d.get("add_fields") or {}
    flt = {f["label"].strip().lower(): f.get("value") for f in d.get("filters") or []}
    terr = extra.get("territory") or d.get("territory") or (flt.get("territory") if isinstance(flt.get("territory"), str) else "")
    dist = extra.get("distributor") or (flt.get("distributor") if isinstance(flt.get("distributor"), str) else "") or d["name"]
    return {"territory": terr or "", "distributor": dist, "sales_rep": extra.get("sales_rep") or ""}


async def _dropdown_options(page, label):
    ctrl, kind = await SA._find_field(page, label)
    if ctrl is None:
        return None
    if kind == "select":
        return [o for o in await ctrl.evaluate("e => [...e.options].map(o => o.text.trim()).filter(Boolean)")
                if not re.match(r"^(select|--)", o, re.I)]
    await SA._open_panel(page, ctrl, kind)
    _ok, opts, _x = await SA._pick(page, "\u0000chimera-none\u0000", True)
    await SA._close_panel(page)
    return [o for o in opts if o and not re.match(r"^(select|no (results|data))", o, re.I)]


async def _dialog(page):
    try:
        return await page.evaluate(_DIALOG_JS)
    except Exception:
        return None


async def _dismiss(page, words=("ok", "close", "cancel")):
    try:
        return await page.evaluate(_CLICK_DIALOG_JS, [list(words)])
    except Exception:
        return False


def _is_error(dlg):
    t = f"{dlg.get('title', '')} {dlg.get('all', '')}".lower()
    return bool(re.search(r"error|not unloaded|cannot|can't|failed|invalid|exceed|required|please", t))


async def _fill_form(page, cfg, st, f, log, rep_required=True):
    """Opens ADD LOADING and fills the header. Returns {'blocked': msg} | {'need_rep': [options]} | {'rep': name}."""
    await page.goto(st["url"], wait_until="domcontentloaded", timeout=st["timeout_ms"])
    await SA._settle(page)
    if await SA._ensure_logged_in(page, cfg, st["timeout_ms"]):
        log("Session had expired — logged in again.")
        await page.goto(st["url"], wait_until="domcontentloaded", timeout=st["timeout_ms"])
        await SA._settle(page)
    deadline = time.time() + st["timeout_ms"] / 1000
    while (await SA._find_field(page, "Distributor"))[0] is None:
        if time.time() > deadline:
            raise AddError("ADD LOADING opened but its 'Distributor' field never appeared — check the Add Loading page "
                           "address in Distributor Loadings › Settings.", "open")
        await page.wait_for_timeout(400)
    today = date.today().isoformat()
    steps = []
    if st["loading_for"]:
        steps.append(("Loading For", st["loading_for"]))
    if st["company"]:
        steps.append(("Product Company", st["company"]))
    if f["territory"]:
        steps.append(("Territory", f["territory"]))
    steps.append(("Distributor", f["distributor"]))
    for label, value in steps:
        try:
            await SA.fill_filter(page, {"label": label, "type": "dropdown", "value": value}, today)
        except SA.StepError as e:
            raise AddError(e.message, "fields")
        log(f"{label} = {value}")
        await page.wait_for_timeout(400)
        dlg = await _dialog(page)
        if dlg and _is_error(dlg):
            await _dismiss(page)
            return {"blocked": dlg.get("text") or dlg.get("all")}
    # Sales Rep: the one asked for, else the only one SFA offers for this distributor
    rep = f["sales_rep"]
    opts, until = [], time.time() + 10      # SFA loads the reps after the distributor is chosen
    while True:
        opts = await _dropdown_options(page, "Sales Rep") or []
        if opts or time.time() > until:
            break
        await page.wait_for_timeout(700)
    if not rep:
        if len(opts) == 1:
            rep = opts[0]
        elif not opts:
            raise AddError(f"SFA lists no Sales Rep for {f['distributor']} on the Add Loading page.", "fields")
        else:
            return {"need_rep": opts}
    elif opts and rep not in opts:
        return {"need_rep": opts, "message": f"'{rep}' isn't offered for this distributor any more — choose the rep."}
    try:
        await SA.fill_filter(page, {"label": "Sales Rep", "type": "dropdown", "value": rep}, today)
    except SA.StepError as e:
        raise AddError(e.message, "fields")
    log(f"Sales Rep = {rep}")
    # SFA answers the rep choice with its checks (e.g. previous loading not unloaded) or with the product table
    deadline = time.time() + max(15, st["timeout_ms"] / 2000)
    while time.time() < deadline:
        await page.wait_for_timeout(600)
        dlg = await _dialog(page)
        if dlg and _is_error(dlg):
            await _dismiss(page)
            return {"blocked": dlg.get("text") or dlg.get("all"), "rep": rep}
        tbl = await page.evaluate(_TABLE_JS, [uuid.uuid4().hex[:8]])
        if tbl["rows"]:
            return {"rep": rep, "table": tbl}
    raise AddError("The product table didn't appear after choosing the Sales Rep.", "table")


async def _browser(st, body):
    from playwright.async_api import async_playwright
    cfg = sfa_config.load_config()
    async with async_playwright() as p:
        browser = await SA._launch(p, cfg.get("browser", "chrome"), st["headless"])
        LA._ACTIVE.update(loop=asyncio.get_running_loop(), browser=browser)
        try:
            context = await browser.new_context(viewport={"width": 1500, "height": 950})
            page = await context.new_page()
            await SA._login(page, cfg, st["timeout_ms"])
            return await body(page, cfg)
        finally:
            LA._ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:
                pass


def _products(tbl):
    out = []
    for r in tbl["rows"]:
        out.append({"name": r["name"], "code": r["code"], "price": _n(r["price"]), "stock": _n(r["stock"]),
                    "last_unloaded": _n(r["last_unloaded"])})
    return out


def _run(fn):
    if not AS.acquire_lock(f"add loading ({datetime.now():%H:%M})"):
        holder = AS.lock_holder() or {}
        return {"ok": False, "status": "busy",
                "message": f"Another SFA run is already in progress ({holder.get('owner', 'unknown')})."}
    try:
        return fn()
    finally:
        AS.release_lock()


def read_form(distributor_id, fields=None, headless=None):
    """Step 1 — read only. Returns products (only those with stock), or blocked / need_rep."""
    d = LS.get_distributor(int(distributor_id))
    if not d:
        return {"ok": False, "message": "That distributor no longer exists."}
    if d.get("blocked"):
        return {"ok": False, "message": d["problems"][0]}
    st = settings()
    if headless is not None:
        st["headless"] = headless
    f = {**fields_for(d), **{k: v for k, v in (fields or {}).items() if k in ("territory", "distributor", "sales_rep")}}
    logs = []

    def log(m):
        logs.append(f"{datetime.now():%H:%M:%S} {m}")

    async def body(page, cfg):
        res = await _fill_form(page, cfg, st, f, log)
        if res.get("table"):
            res["products"] = [p for p in _products(res["table"]) if p["stock"] > 0]
            res["hidden_zero"] = sum(1 for p in _products(res["table"]) if p["stock"] <= 0)
            res["number"] = res["table"].get("number", "")
            res.pop("table")
        return res

    def go():
        try:
            res = asyncio.run(_browser(st, body))
        except AddError as e:
            return {"ok": False, "message": e.message, "fields": f, "log": logs}
        except Exception as e:
            return {"ok": False, "message": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}", "fields": f, "log": logs}
        if res.get("blocked"):
            log("SFA: " + res["blocked"])
            return {"ok": False, "blocked": True, "message": res["blocked"], "fields": {**f, "sales_rep": res.get("rep", "")},
                    "log": logs}
        if res.get("need_rep"):
            return {"ok": False, "need_rep": res["need_rep"], "message": res.get("message") or
                    f"SFA offers {len(res['need_rep'])} sales reps for {f['distributor']} — choose one.", "fields": f,
                    "log": logs}
        f["sales_rep"] = res["rep"]
        LS.set_add_fields(d["id"], f)
        log(f"{len(res['products'])} product(s) with stock" +
            (f"; {res['hidden_zero']} with no stock left out" if res["hidden_zero"] else "") + ".")
        return {"ok": True, "fields": f, "products": res["products"], "hidden_zero": res["hidden_zero"],
                "number": res.get("number", ""), "dry_run": st["dry_run"], "log": logs}
    return _run(go)


def check_quantities(products, qty):
    """qty {code: number}. Problems as text (empty = fine)."""
    by = {p["code"]: p for p in products}
    bad = []
    for code, q in qty.items():
        q = _n(q)
        if q < 0:
            bad.append(f"{code}: quantity can't be negative")
        elif code not in by:
            if q > 0:
                bad.append(f"{code} isn't on the Add Loading page any more")
        elif q > by[code]["stock"]:
            bad.append(f"{by[code]['name']}: {q:g} wanted, only {by[code]['stock']:g} in stock now")
    if not any(_n(q) > 0 for q in qty.values()):
        bad.append("Enter at least one quantity.")
    return bad


def create(distributor_id, quantities, fields=None, dry_run=None, headless=None):
    """Step 2 — fills the quantities and saves (or stops before SAVE in dry-run mode)."""
    d = LS.get_distributor(int(distributor_id))
    if not d:
        return {"ok": False, "message": "That distributor no longer exists."}
    if d.get("blocked"):
        return {"ok": False, "message": d["problems"][0]}
    st = settings()
    if headless is not None:
        st["headless"] = headless
    dry = st["dry_run"] if dry_run is None else bool(dry_run)
    f = {**fields_for(d), **{k: v for k, v in (fields or {}).items() if k in ("territory", "distributor", "sales_rep")}}
    qty = {str(k): _n(v) for k, v in (quantities or {}).items()}
    logs = []

    def log(m):
        logs.append(f"{datetime.now():%H:%M:%S} {m}")

    async def body(page, cfg):
        native = []
        page.on("dialog", lambda dl: (native.append(dl.message), asyncio.ensure_future(dl.accept())))
        res = await _fill_form(page, cfg, st, f, log)
        if res.get("blocked") or res.get("need_rep"):
            return res
        tbl = res["table"]
        prods = _products(tbl)
        bad = check_quantities(prods, qty)
        if bad:
            return {"blocked": "Not saved — the numbers changed in SFA: " + "; ".join(bad)}
        for r in tbl["rows"]:
            q = qty.get(r["code"], 0)
            if q <= 0:
                continue
            box = page.locator(f"[data-chimera-q='{r['qref']}']").first
            await box.click()
            await box.fill("")
            await box.type(f"{q:g}", delay=15)
            await box.dispatch_event("change")
            await box.press("Tab")
        await page.wait_for_timeout(500)
        check = await page.evaluate(_TABLE_JS, [uuid.uuid4().hex[:8]])
        typed = {r["code"]: _n(r["qty"]) for r in check["rows"]}
        wrong = [c for c, q in qty.items() if q > 0 and abs(typed.get(c, -1) - q) > 1e-9]
        if wrong:
            return {"blocked": "Not saved — SFA didn't take the quantity for: " + ", ".join(wrong)}
        number = check.get("number") or tbl.get("number", "")
        os.makedirs(SHOT_DIR, exist_ok=True)
        shot = os.path.join(SHOT_DIR, f"{re.sub(r'[^A-Za-z0-9]+', '_', d['name'])}_{datetime.now():%Y%m%d_%H%M%S}"
                                      f"{'_dry_run' if dry else ''}.png")
        await page.screenshot(path=shot, full_page=True)
        log(f"Quantities typed for {sum(1 for q in qty.values() if q > 0)} product(s); loading number {number or '?'}.")
        if dry:
            log("Dry run — stopped before SAVE. Nothing was saved in SFA.")
            return {"dry_run": True, "number": number, "screenshot": shot}
        btn, _lbl, seen = await SA._find_button(page, "SAVE", 10000)
        if btn is None:
            raise AddError("No SAVE button found. Buttons on the page: " + "; ".join(seen[:12]), "save")
        await btn.click()
        log("Pressed SAVE.")
        outcome, deadline, answered = None, time.time() + max(20, st["timeout_ms"] / 1000), False
        while time.time() < deadline and outcome is None:
            await page.wait_for_timeout(600)
            dlg = await _dialog(page)
            if dlg:
                txt = f"{dlg.get('title', '')} {dlg.get('text', '')}".strip()
                low = txt.lower()
                if not answered and re.search(r"are you sure|confirm|do you want", low) and not _is_error(dlg):
                    clicked = await _dismiss(page, ("yes", "ok", "confirm", "yes, save it!", "save"))
                    log(f"SFA asked: '{txt}' — answered {clicked or '?'}.")
                    answered = True
                    continue
                if _is_error(dlg):
                    await _dismiss(page)
                    outcome = ("error", txt)
                elif re.search(r"success|added|saved|created", low):
                    await _dismiss(page, ("ok", "close"))
                    outcome = ("ok", txt)
            elif native:
                m = native[-1]
                outcome = ("error", m) if re.search(r"error|cannot|failed|exceed", m, re.I) else ("ok", m)
            elif "/loading/create" not in page.url:
                outcome = ("ok", "Left the Add Loading page after saving.")
        if outcome is None:
            outcome = ("unknown", "No message from SFA after SAVE.")
        log(f"SFA: {outcome[1]}")
        if outcome[0] == "error":
            return {"blocked": f"SFA didn't save the loading: {outcome[1]}", "screenshot": shot}
        # Is the new loading listed as pending on View Loadings?
        verified = False
        try:
            lp = await page.context.new_page()
            r = await LA._open_and_search(lp, cfg, LA._settings()["url"], d, 0, st["timeout_ms"], lane_steps=False)
            verified = any(x["pending"] and (not number or x["number"] == number) for x in r.get("rows") or [])
            await lp.close()
        except Exception as e:
            log(f"Couldn't check View Loadings: {type(e).__name__}")
        log("View Loadings shows it as pending ✓" if verified else "Not yet visible as pending on View Loadings.")
        return {"saved": True, "number": number, "verified": verified, "sfa_message": outcome[1], "screenshot": shot}

    def go():
        try:
            res = asyncio.run(_browser(st, body))
        except AddError as e:
            return {"ok": False, "message": e.message, "log": logs}
        except Exception as e:
            return {"ok": False, "message": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}", "log": logs}
        if res.get("blocked"):
            return {"ok": False, "blocked": True, "message": res["blocked"], "screenshot": res.get("screenshot"), "log": logs}
        if res.get("need_rep"):
            return {"ok": False, "need_rep": res["need_rep"], "message": "Choose the sales rep again.", "log": logs}
        LS.record_add(d, res, dry)
        if res.get("dry_run"):
            return {"ok": True, "dry_run": True, "number": res.get("number"), "screenshot": res["screenshot"],
                    "message": "Dry run done — Chimera filled the Add Loading page and stopped before SAVE. "
                               "Nothing was saved in SFA. Check the screenshot.", "log": logs}
        return {"ok": True, "saved": True, "verified": res["verified"], "number": res.get("number"),
                "screenshot": res.get("screenshot"),
                "message": (f"Loading {res.get('number') or ''} saved in SFA"
                            + (" and listed as pending on View Loadings." if res["verified"] else
                               " (SFA said: " + res["sfa_message"] + "). It isn't visible on View Loadings yet — check SFA.")
                            + " Confirm it through Prepare → Approve Loadings."), "log": logs}
    return _run(go)
