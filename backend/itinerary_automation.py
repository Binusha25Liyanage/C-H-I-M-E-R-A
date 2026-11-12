"""
itinerary_automation.py
Reads each working territory's monthly itinerary from SFA. Read only —
nothing is changed in SFA.

  ITINERARY > SR Individual Itinerary > View SR Individual Itinerary
  (/it/month-itinerary) -> Year, Month, Territory -> SEARCH -> the
  territory's row (one rep per territory) -> the eye icon -> the month
  calendar (/it/month-itinerary/<id>/false): each day shows a badge
  (WD = working day, H = holiday), the day number and the route ('-' =
  no route that day).

Several territories are read at once (one browser tab each) after a single
login. The day being prepared follows outlet_store.target_day(): from the
prepare time (23:59) it's tomorrow — and if Chimera wasn't running then,
the read simply happens the next time it is (ensure_prepared).
"""

import asyncio
import calendar
import os
import re
import threading
import time
import uuid
from datetime import date, datetime

import automation_store as AS
import outlet_store as OS
import sfa_automation as SA
import sfa_config

STEPS = ["login", "open", "filters", "search", "calendar", "read"]
STEP_LABELS = {"login": "Log in", "open": "Open", "filters": "Filters", "search": "Search",
               "calendar": "Open calendar", "read": "Read days"}
SHOT_ROOT = os.path.join(os.path.expanduser("~"), "Chimera", "screenshots", "itinerary")

LIVE = SA.LiveState("itinerary")
_ACTIVE = {"loop": None, "browser": None}
_AUTO = {"last": 0.0, "boundary": None}
AUTO_MIN_GAP = 120       # seconds between automatic attempts (the scheduler ticks every 30 s)


class StepError(Exception):
    def __init__(self, step, message):
        super().__init__(message)
        self.step, self.message = step, message


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Page readers
# ---------------------------------------------------------------------------
_ROWS_JS = r"""
([token]) => {
  // View SR Individual Itinerary results: YEAR | MONTH | SALES REP | ... | ACTION (eye, pencil)
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  for (const t of [...document.querySelectorAll('table')].filter(vis)) {
    const heads = [...t.querySelectorAll('thead th, thead td')];
    const hs = (heads.length ? heads : [...(t.querySelector('tr') || {children: []}).children]).map(h => norm(h.innerText).toLowerCase());
    const col = re => hs.findIndex(h => re.test(h));
    const iY = col(/^year/), iM = col(/^month/), iR = col(/sales\s*rep|^rep/), iA = col(/^action/);
    if (iR < 0 || (iM < 0 && iY < 0)) continue;
    const rows = [];
    [...t.querySelectorAll('tbody tr')].forEach((r, k) => {
      const c = r.children; if (c.length <= Math.max(iR, iM)) return;
      const cell = iA >= 0 ? c[iA] : r;
      const cands = [...cell.querySelectorAll('a, button, i, svg, span, [onclick]')];
      const eye = cands.find(e => /eye|view/i.test((e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className) + ' ' +
                     (e.getAttribute('title') || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('href') || '') + ' ' + (e.getAttribute('mattooltip') || ''))
                     && !/pencil|edit/i.test((e.className || '') + (e.getAttribute('title') || '')))
                  || cands.find(e => /\/false$/.test(e.getAttribute('href') || ''))
                  || cands[0];
      if (eye) (eye.closest('a, button') || eye).setAttribute('data-chimera-eye', token + '-' + k);
      rows.push({year: iY >= 0 ? norm(c[iY].innerText) : '', month: iM >= 0 ? norm(c[iM].innerText) : '',
                 rep: norm(c[iR].innerText), eye: eye ? token + '-' + k : ''});
    });
    return {table: true, rows};
  }
  const empty = /no (data|records?|matching)/i.test(document.body.innerText || '');
  return {table: false, rows: [], empty};
}
"""

_CAL_JS = r"""
() => {
  // The month calendar: header fields + every day cell (badge WD/H, day number, route)
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const field = (re) => {
    for (const l of [...document.querySelectorAll('label, span, div, p')].filter(vis)) {
      if (l.children.length > 1 || !re.test(norm(l.textContent))) continue;
      // the first visible box AFTER this label (not just the first box in a shared row)
      let box = l.parentElement;
      for (let up = 0; up < 3 && box; up++, box = box.parentElement) {
        const inp = [...box.querySelectorAll('input, select')].find(i => vis(i) &&
                      (l.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_FOLLOWING));
        if (inp) return norm(inp.value || inp.textContent);
      }
    }
    return '';
  };
  const header = {year: field(/^year$/i), month: field(/^month$/i), rep: field(/^sales\s*rep$/i), distributor: field(/^distributor$/i)};
  const leaves = [...document.querySelectorAll('body *')].filter(e => vis(e) && !e.children.length);
  const badges = leaves.filter(e => /^(WD|H|HD|HOL|PH|PD|OD|L)$/i.test(norm(e.textContent)));
  const cells = new Map();
  for (const b of badges) {
    let cell = b.parentElement, num = null;
    for (let up = 0; up < 4 && cell; up++, cell = cell.parentElement) {
      num = [...cell.querySelectorAll('*')].find(e => !e.children.length && vis(e) && /^\d{1,2}$/.test(norm(e.textContent)));
      if (num) break;
    }
    if (!num || cells.has(cell)) continue;
    const day = parseInt(norm(num.textContent), 10);
    const lines = (cell.innerText || '').split('\n').map(norm).filter(Boolean);
    const badge = norm(b.textContent).toUpperCase();
    const rest = []; let skippedB = false, skippedN = false;
    for (const l of lines) {
      if (!skippedB && l.toUpperCase() === badge) { skippedB = true; continue; }
      if (!skippedN && l === String(day)) { skippedN = true; continue; }
      if (/^today$/i.test(l)) continue;
      rest.push(l);
    }
    cells.set(cell, {day, badge, route: rest.join(' ')});
  }
  const days = [...cells.values()].sort((a, b) => a.day - b.day);
  const bar = [...document.querySelectorAll('body *')].filter(vis).map(e => norm(e.textContent))
                .find(t => /^(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{4}$/i.test(t)) || '';
  return {header, days, bar, url: location.href};
}
"""


def parse_calendar(raw, year, month):
    """Checks one calendar reading and turns it into {'YYYY-MM-DD': (badge, route)}. Raises StepError."""
    want = calendar.monthrange(year, month)[1]
    h = raw.get("header") or {}
    shown_month = OS.parse_month_value(h.get("month")) or OS.parse_month_value((raw.get("bar") or "").split(" ")[0])
    if shown_month and shown_month != month:
        raise StepError("read", f"The calendar shows {OS.MONTHS[shown_month - 1].title()}, not "
                                f"{OS.MONTHS[month - 1].title()} — SFA opened the wrong itinerary.")
    shown_year = (h.get("year") or "").strip()
    if shown_year.isdigit() and int(shown_year) != year:
        raise StepError("read", f"The calendar shows {shown_year}, not {year}.")
    days = {}
    for c in raw.get("days") or []:
        d = int(c["day"])
        if 1 <= d <= want and d not in days:
            badge = "H" if c["badge"].upper().startswith("H") else c["badge"].upper()
            route = (c.get("route") or "").strip()
            days[f"{year:04d}-{month:02d}-{d:02d}"] = (badge, "" if badge == "H" else route)
    if len(days) < want:
        missing = [d for d in range(1, want + 1) if f"{year:04d}-{month:02d}-{d:02d}" not in days]
        raise StepError("read", f"Read {len(days)} of {want} days from the calendar (missing "
                                f"{', '.join(map(str, missing[:8]))}{'…' if len(missing) > 8 else ''}). "
                                "The calendar layout may have changed — send a screenshot.")
    return days


async def _shot(page, run_id, name):
    try:
        folder = os.path.join(SHOT_ROOT, f"run_{run_id}")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, re.sub(r"[^\w\- ]", "_", name) + ".png")
        await page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


async def _search(page, cfg, url, territory, year, month, idx, timeout_ms):
    LIVE.step(idx, "open", "active")
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    await SA._settle(page)
    if await SA._ensure_logged_in(page, cfg, timeout_ms):
        LIVE.log("Session had expired in this tab — logged in again.", lane=territory)
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await SA._settle(page)
    deadline = time.time() + timeout_ms / 1000
    while (await SA._find_field(page, "Territory"))[0] is None:
        if time.time() > deadline:
            raise StepError("open", "View SR Individual Itinerary opened but the 'Territory' filter never appeared — "
                                    "check the itinerary page address in Outlet Updates › Settings.")
        if LIVE.cancelled():
            raise Cancelled()
        await page.wait_for_timeout(400)
    LIVE.step(idx, "open", "done")
    LIVE.step(idx, "filters", "active")
    filters = [{"label": "Year", "type": "text", "value": str(year)},
               {"label": "Month", "type": "dropdown", "value": OS.MONTHS[month - 1]},
               {"label": "Territory", "type": "dropdown", "value": territory}]
    for f in filters:
        if LIVE.cancelled():
            raise Cancelled()
        if f["label"] == "Year":
            ctrl, _k = await SA._find_field(page, "Year")
            if ctrl is not None and (await ctrl.input_value()).strip() == str(year):
                continue       # already shows the right year
        try:
            await SA.fill_filter(page, f, date.today().isoformat())
        except SA.StepError as e:
            raise StepError("filters", e.message)
    LIVE.log(f"Year {year} · {OS.MONTHS[month - 1].title()} · Territory {territory}", lane=territory)
    LIVE.step(idx, "filters", "done")
    LIVE.step(idx, "search", "active")
    btn, _lbl, seen = await SA._find_button(page, "SEARCH", 10000)
    if btn is None:
        raise StepError("search", "No SEARCH button found. Buttons on the page: " + ("; ".join(seen[:12]) or "none"))
    await btn.click()
    deadline = time.time() + max(20, timeout_ms / 1000)
    res = None
    while time.time() < deadline:
        await page.wait_for_timeout(700)
        try:
            res = await page.evaluate(_ROWS_JS, [uuid.uuid4().hex[:8]])
        except Exception:
            continue
        if res["table"] or res.get("empty"):
            break
        if LIVE.cancelled():
            raise Cancelled()
    ctrl, _k = await SA._find_field(page, "Territory")
    shown = (await SA._control_text(ctrl)).strip() if ctrl is not None else ""
    if shown and territory.lower() not in shown.lower():
        raise StepError("search", f"SFA's Territory filter showed '{shown}' instead of '{territory}' after SEARCH — "
                                  "results not used.")
    LIVE.step(idx, "search", "done")
    return res or {"table": False, "rows": []}


async def _read_month(context, cfg, url, territory, year, month, idx, opts):
    """One territory, one month. Returns (status, days, info)."""
    page = await context.new_page()
    try:
        res = await _search(page, cfg, url, territory, year, month, idx, opts["timeout_ms"])
        want = OS.MONTHS[month - 1]
        rows = [r for r in res["rows"] if (not r["month"] or OS.parse_month_value(r["month"]) == month)
                and (not r["year"] or r["year"] == str(year))]
        if not rows:
            for s in ("calendar", "read"):
                LIVE.step(idx, s, "skipped")
            return "missing", {}, {"message": f"SFA has no {want.title()} {year} itinerary for {territory} yet."}
        if len(rows) > 1:
            named = [r for r in rows if territory.lower() in r["rep"].lower()]
            if len(named) != 1:
                raise StepError("search", f"SFA listed {len(rows)} itineraries for {territory} "
                                          f"({', '.join(r['rep'] for r in rows[:6])}) — Chimera won't guess which is right.")
            LIVE.log(f"{len(rows)} itineraries listed — using {named[0]['rep']} (its name matches {territory}).",
                     lane=territory, level="warn")
            rows = named
        row = rows[0]
        LIVE.step(idx, "calendar", "active")
        target = page
        el = page.locator(f"[data-chimera-eye='{row['eye']}']").first
        if not row["eye"] or not await el.count():
            raise StepError("calendar", "Couldn't find the eye (view) icon in the itinerary row.")
        try:
            async with context.expect_page(timeout=2500) as pinfo:
                await el.click(timeout=6000)
            target = await pinfo.value
        except Exception:
            pass       # opened in the same tab
        raw, deadline = None, time.time() + opts["timeout_ms"] / 1000
        while time.time() < deadline:
            await target.wait_for_timeout(700)
            try:
                raw = await target.evaluate(_CAL_JS)
            except Exception:
                continue
            if len(raw.get("days") or []) >= 28:
                break
            if LIVE.cancelled():
                raise Cancelled()
        LIVE.step(idx, "calendar", "done")
        LIVE.step(idx, "read", "active")
        days = parse_calendar(raw or {}, year, month)
        h = (raw or {}).get("header") or {}
        if target is not page:
            await target.close()
        LIVE.step(idx, "read", "done")
        return "ok", days, {"rep": h.get("rep") or row["rep"], "distributor": h.get("distributor", ""),
                            "sfa_url": (raw or {}).get("url", "")}
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def _lane(browser, state, cfg, url, job, idx, opts, sem, seen):
    """One territory. Runs in its OWN browser context (cookies + storage copied from the single login):
    SFA keeps search filters in the browser, so tabs sharing one context could swap each other's results."""
    async with sem:
        territory, months = job["territory"], job["months"]
        if LIVE.cancelled():
            LIVE.lane(idx, status="cancelled", message="Stopped before it started.")
            return
        LIVE.lane(idx, status="running", started=time.time(), steps={s: ("done" if s == "login" else "pending") for s in STEPS})
        notes = []
        context = await browser.new_context(storage_state=state, viewport={"width": 1500, "height": 950})
        try:
            for (y, m) in months:
                st, days, info = await _read_month(context, cfg, url, territory, y, m, idx, opts)
                mk = f"{y:04d}-{m:02d}"
                if st == "ok":
                    seen.setdefault((mk, info.get("sfa_url", "")), []).append(territory)
                OS.save_month(territory, mk, st, days, rep=info.get("rep", ""), distributor=info.get("distributor", ""),
                              sfa_url=info.get("sfa_url", ""), message=info.get("message", ""))
                if st == "ok":
                    work = sum(1 for b, r in days.values() if b != "H" and r and r != "-")
                    hol = sum(1 for b, _ in days.values() if b == "H")
                    notes.append(f"{OS.MONTHS[m - 1].title()}: {work} route days, {hol} holidays")
                    LIVE.log(f"{OS.MONTHS[m - 1].title()} {y}: {len(days)} days read — {work} with a route, {hol} "
                             f"holiday(s). Rep {info.get('rep') or '?'}.", lane=territory)
                else:
                    notes.append(info["message"])
                    LIVE.log(info["message"], lane=territory, level="warn")
                if len(months) > 1:
                    for s in STEPS[1:]:
                        LIVE.step(idx, s, "pending")
            status = "done" if all(not n.startswith("SFA has no") for n in notes) else "missing"
            LIVE.lane(idx, status=status, message=" · ".join(notes),
                      elapsed=round(time.time() - LIVE.snapshot()["lanes"][idx]["started"], 1))
        except Cancelled:
            LIVE.lane(idx, status="cancelled", message="Stopped.")
        except Exception as e:
            msg = e.message if isinstance(e, StepError) else f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
            step = e.step if isinstance(e, StepError) else "open"
            if LIVE.cancelled():
                LIVE.lane(idx, status="cancelled", message="Stopped.")
                return
            for (y, m) in months:
                OS.save_month(territory, f"{y:04d}-{m:02d}", "failed", message=msg)
            LIVE.step(idx, step, "failed")
            LIVE.lane(idx, status="failed", message=msg)
            LIVE.log(msg, lane=territory, level="error")
        finally:
            try:
                await context.close()
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
            n = len(LIVE.snapshot()["lanes"])
            for i in range(n):
                LIVE.step(i, "login", "active")
            try:
                _ok, msg = await SA._login(page, cfg, opts["timeout_ms"])
            except Exception as e:
                msg = e.message if isinstance(e, SA.StepError) else f"Couldn't reach SFA: {str(e).splitlines()[0]}"
                for i in range(n):
                    LIVE.step(i, "login", "failed")
                    LIVE.lane(i, status="failed", message=msg)
                LIVE.log(msg, level="error")
                return False, msg
            LIVE.log(msg)
            for i in range(n):
                LIVE.step(i, "login", "done")
            state = await context.storage_state()
            await page.close()
            await body(browser, state)
            return True, ""
        finally:
            _ACTIVE.update(loop=None, browser=None)
            try:
                await browser.close()
            except Exception:
                pass


def settings():
    s = AS.get_automation_settings()
    return {"url": s.get("ou_itinerary_url") or OS.DEFAULT_ITINERARY_URL,
            "tabs": max(1, min(AS.MAX_PARALLEL_TABS, int(s.get("ou_tabs") or 6))),
            "prepare_time": s.get("ou_prepare_time") or "23:59",
            "timeout_ms": int(s.get("page_timeout_seconds") or 60) * 1000,
            "headless": s.get("headless", "1") == "1",
            "auto": s.get("auto_outlet_updates", "1") == "1", "paused": s.get("paused") == "1"}


def run_read(territories=None, months=None, trigger="manual", headless=None):
    """Reads the itinerary of the given territories (all working ones if None) for the given months
    [(year, month)] — by default the month of the day being prepared."""
    st = settings()
    if headless is not None:
        st["headless"] = headless
    cfg = sfa_config.load_config()
    day = OS.target_day(None, st["prepare_time"])
    months = months or [(day.year, day.month)]
    names = territories if territories is not None else [t["territory"] for t in OS.list_territories(enabled_only=True)]
    lanes = [{"name": n, "status": "queued", "message": "", "steps": {s: "pending" for s in STEPS}, "elapsed": 0,
              "started": None} for n in names]
    if not names:
        LIVE.reset(kind="itinerary", trigger=trigger)
        msg = "No working territories — switch some on in Outlet Updates › Working territories."
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "failed", "message": msg})
        return {"ok": False, "message": msg}
    if not AS.acquire_lock(f"itinerary ({datetime.now():%H:%M})"):
        holder = AS.lock_holder() or {}
        msg = f"Another SFA run is already in progress ({holder.get('owner', 'unknown')})."
        LIVE.reset(kind="itinerary", trigger=trigger)
        LIVE.set(active=False, phase="done", result={"ok": False, "status": "busy", "message": msg})
        return {"ok": False, "status": "busy", "message": msg}
    LIVE.reset(kind="itinerary", trigger=trigger, target_day=day.isoformat(), steps=STEPS, lanes=lanes)
    rid = OS.start_run("itinerary", trigger, day.isoformat())
    LIVE.set(run_id=rid)
    try:
        if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
            return _finish(rid, "failed", "SFA login isn't set up — Settings › SFA Connection.")
        label = ", ".join(f"{OS.MONTHS[m - 1].title()} {y}" for y, m in months)
        LIVE.log(f"Reading the {label} itinerary of {len(names)} territor{'y' if len(names) == 1 else 'ies'}, "
                 f"up to {st['tabs']} at a time. Nothing is changed in SFA.")
        opts = {"run_id": rid, "timeout_ms": st["timeout_ms"], "headless": st["headless"]}
        jobs = [{"territory": n, "months": months} for n in names]

        async def body(browser, state):
            LIVE.set(phase="reading")
            seen = {}
            sem = asyncio.Semaphore(st["tabs"])
            await asyncio.gather(*[_lane(browser, state, cfg, st["url"], j, i, opts, sem, seen) for i, j in enumerate(jobs)])
            dups = _duplicates(seen)
            if dups and not LIVE.cancelled():
                LIVE.log("SFA gave the same itinerary to " + "; ".join(" & ".join(g) for g in dups.values()) +
                         " — reading them again one at a time to make sure.", level="warn")
                again = sorted({t for g in dups.values() for t in g})
                seen2, one = {}, asyncio.Semaphore(1)
                for i, j in enumerate(jobs):
                    if j["territory"] in again:
                        await _lane(browser, state, cfg, st["url"], j, i, opts, one, seen2)
                for (mk, url_), group in _duplicates(seen2).items():
                    msg = (f"SFA shows the same itinerary for {', '.join(group)} ({url_.rsplit('/', 2)[-2] if url_ else '?'}). "
                           "Not used — check these territories' itineraries in SFA.")
                    for t in group:
                        OS.save_month(t, mk, "failed", message=msg, clear=True)
                        k = next(i for i, j in enumerate(jobs) if j["territory"] == t)
                        LIVE.lane(k, status="failed", message=msg)
                    LIVE.log(msg, level="error")

        asyncio.run(_with_browser(cfg, opts, body))
        ls = LIVE.snapshot()["lanes"]
        ok = sum(l["status"] == "done" for l in ls)
        miss = sum(l["status"] == "missing" for l in ls)
        bad = sum(l["status"] == "failed" for l in ls)
        msg = (f"{ok} territor{'y' if ok == 1 else 'ies'} read" + (f", {miss} with no itinerary in SFA yet" if miss else "")
               + (f", {bad} failed" if bad else "") + ".")
        status = "cancelled" if LIVE.cancelled() else ("done" if ok or miss else "failed")
        return _finish(rid, status, msg)
    except Exception as e:
        return _finish(rid, "failed", f"Unexpected error: {e}")


def _duplicates(seen):
    """{(month, sfa itinerary url): [territories]} -> only the ones shared by more than one territory."""
    return {k: v for k, v in seen.items() if k[1] and len(v) > 1}


def _finish(rid, status, msg):
    OS.finish_run(rid, status, msg, log_text())
    LIVE.set(active=False, phase="done", result={"ok": status == "done", "status": status, "message": msg, "run_id": rid})
    LIVE.log(msg, level="info" if status == "done" else "warn")
    AS.release_lock()
    return {"ok": status == "done", "status": status, "message": msg, "run_id": rid}


def ensure_prepared(now=None, trigger="catch-up"):
    """Called by the in-app scheduler: if the day being prepared still needs its itinerary read for some working
    territory, read it now. Safe to call every tick — returns quickly when there's nothing to do."""
    st = settings()
    if not st["auto"] or st["paused"]:
        return None
    day, due = OS.due_territories(now, st["prepare_time"])
    if not due:
        return None
    boundary = OS.prepare_boundary(now, st["prepare_time"])
    if _AUTO["boundary"] == boundary and time.time() - _AUTO["last"] < AUTO_MIN_GAP:
        return None
    _AUTO.update(boundary=boundary, last=time.time())
    late = ((now or datetime.now()) - boundary).total_seconds() > 15 * 60
    trig = "catch-up" if late else "nightly"
    return run_read(due, [(day.year, day.month)], trigger=trig if trigger == "catch-up" else trigger)


def start_background(**kw):
    import loading_automation
    if LIVE.snapshot().get("active") or SA.LIVE.snapshot().get("active") or loading_automation.LIVE.snapshot().get("active"):
        return {"ok": False, "message": "Another SFA run is already in progress."}
    LIVE.reset(kind="itinerary", trigger=kw.get("trigger", "manual"))
    threading.Thread(target=run_read, kwargs=kw, daemon=True, name="chimera-itinerary").start()
    return {"ok": True, "message": "Started."}


_TERR_TABLE_JS = r"""
() => {
  // View Territory: REGION … | AREA NAME | SUPERVISOR AREA NAME | TERRITORY CODE | TERRITORY NAME | … | STATUS
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  for (const t of [...document.querySelectorAll('table')].filter(vis)) {
    const heads = [...t.querySelectorAll('thead tr:last-child th, thead tr:last-child td')];
    let hs = heads.map(h => norm(h.innerText).toLowerCase());
    if (!hs.some(h => /territory\s*name/.test(h))) hs = [...t.querySelectorAll('thead th')].map(h => norm(h.innerText).toLowerCase());
    const col = re => hs.findIndex(h => re.test(h));
    const iN = col(/territory\s*name/), iC = col(/territory\s*code/), iR = col(/region\s*name/), iA = col(/^area\s*name/),
          iS = col(/supervisor\s*area\s*name/), iSt = col(/^status/);
    if (iN < 0) continue;
    const rows = [...t.querySelectorAll('tbody tr')].map(r => r.children).filter(c => c.length > iN).map(c => ({
      territory: norm(c[iN].innerText), code: iC >= 0 ? norm(c[iC].innerText) : '', region: iR >= 0 ? norm(c[iR].innerText) : '',
      area: iA >= 0 ? norm(c[iA].innerText) : '', supervisor_area: iS >= 0 ? norm(c[iS].innerText) : '',
      sfa_status: iSt >= 0 ? norm(c[iSt].innerText).toUpperCase() : ''})).filter(r => r.territory);
    const info = (document.body.innerText.match(/showing\s+([\d,]+)\s*(?:-|to)\s*([\d,]+)\s+of\s+([\d,]+)/i) || []).slice(1).map(x => +x.replace(/,/g, ''));
    // the "Next" pager button
    const nexts = [...document.querySelectorAll('a, button, li, span')].filter(e => vis(e) && /^next$/i.test(norm(e.textContent)));
    const next = nexts[nexts.length - 1];
    let nextOk = false;
    if (next) {
      const box = next.closest('li') || next;
      nextOk = !(/disabled/i.test(box.className + ' ' + next.className) || next.disabled || box.getAttribute('aria-disabled') === 'true'
                 || next.getAttribute('aria-disabled') === 'true');
      if (nextOk) (next.closest('a, button') || next).setAttribute('data-chimera-next', '1');
    }
    return {table: true, rows, info, nextOk, first: rows.length ? rows[0].code + rows[0].territory : ''};
  }
  return {table: false, rows: [], info: [], nextOk: false, first: ''};
}
"""

_SHOW_MAX_JS = r"""
() => {
  // "Show [10] entries": choose the biggest page size if it's a normal <select>
  for (const sel of document.querySelectorAll('select')) {
    const opts = [...sel.options].map(o => +o.value || +o.text).filter(n => n > 0);
    if (opts.length && opts.every(n => [5, 10, 15, 20, 25, 50, 100, 200, 500, 1000].includes(n))) {
      const max = Math.max(...opts);
      const o = [...sel.options].find(o => (+o.value || +o.text) === max);
      sel.value = o.value; sel.dispatchEvent(new Event('change', {bubbles: true})); return max;
    }
  }
  return 0;
}
"""


async def _read_territory_page(cfg, url, headless, timeout_ms):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await SA._launch(p, cfg.get("browser", "chrome"), headless)
        try:
            page = await (await browser.new_context(viewport={"width": 1500, "height": 950})).new_page()
            await SA._login(page, cfg, timeout_ms)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await SA._settle(page)
            if await SA._ensure_logged_in(page, cfg, timeout_ms):
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                await SA._settle(page)
            btn, _l, _seen = await SA._find_button(page, "SEARCH", 8000)
            if btn is not None:
                await btn.click()
            res, deadline = None, time.time() + max(25, timeout_ms / 1000)
            while time.time() < deadline:
                await page.wait_for_timeout(800)
                try:
                    res = await page.evaluate(_TERR_TABLE_JS)
                except Exception:
                    continue
                if res["table"] and res["rows"]:
                    break
            if not res or not res["rows"]:
                raise StepError("open", "View Territory opened but no territory table appeared — check its address in "
                                        "Outlet Updates › Settings.")
            if await page.evaluate(_SHOW_MAX_JS):
                await page.wait_for_timeout(1500)
                res = await page.evaluate(_TERR_TABLE_JS)
            found = {}
            for _ in range(300):                       # pages
                for r in res["rows"]:
                    found.setdefault(r["code"] or r["territory"], r)
                if not res["nextOk"]:
                    break
                before = res["first"]
                await page.locator("[data-chimera-next='1']").last.click()
                for _w in range(30):
                    await page.wait_for_timeout(300)
                    try:
                        res = await page.evaluate(_TERR_TABLE_JS)
                    except Exception:
                        continue
                    if res["first"] != before:
                        break
                else:
                    break                               # the page didn't change — stop
            total = res["info"][2] if len(res.get("info") or []) == 3 else None
            return list(found.values()), total
        finally:
            await browser.close()


def detect_territories(headless=True):
    """Lists SFA's territories. First from Sales Organization › Territory › View Territory (every page: code,
    region, area, supervisor area, Active/Inactive); if that fails, from the itinerary page's Territory list.
    New territories are added switched off; nothing changes in SFA."""
    cfg = sfa_config.load_config()
    if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
        return {"ok": False, "message": "SFA login isn't set up — Settings › SFA Connection."}
    s = AS.get_automation_settings()
    turl = s.get("ou_territory_url") or OS.DEFAULT_TERRITORY_URL
    timeout_ms = int(s.get("page_timeout_seconds") or 60) * 1000
    first_err = ""
    try:
        rows, total = asyncio.run(_read_territory_page(cfg, turl, headless, timeout_ms))
        added = OS.merge_detected(rows)
        active = sum(1 for r in rows if r["sfa_status"].startswith("ACTIVE"))
        note = f" (SFA says {total} in all)" if total and total != len(rows) else ""
        return {"ok": True, "found": len(rows), "added": added, "source": "View Territory",
                "message": f"Read {len(rows)} territories from View Territory{note} — {active} active in SFA"
                           + (f", {len(added)} new (switched off)." if added else ".")}
    except Exception as e:
        first_err = e.message if isinstance(e, StepError) else str(e).splitlines()[0][:200]
    res = SA.detect_filters(settings()["url"], headless=headless)
    if not res.get("ok"):
        return {"ok": False, "message": f"View Territory: {first_err}. Itinerary page: {res.get('message')}"}
    f = next((x for x in res["fields"] if x["label"].strip().lower() == "territory"), None)
    opts = [o for o in ((f or {}).get("options") or (f or {}).get("native_options") or []) if o and o.lower() != "select"]
    if not opts:
        return {"ok": False, "message": f"View Territory: {first_err}. The itinerary page's Territory list was empty too — "
                                        "add territories by name instead."}
    added = OS.merge_detected(opts)
    return {"ok": True, "found": len(opts), "added": added, "source": "itinerary page",
            "message": f"View Territory didn't work ({first_err}), so the itinerary page's list was used: "
                       f"{len(opts)} territories" + (f", {len(added)} new (switched off)." if added else ".")}


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
    out = [f"Chimera Outlet Updates — itinerary read #{s.get('run_id') or '-'} · {s.get('trigger', '')} · "
           f"preparing {s.get('target_day', '')}"]
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
