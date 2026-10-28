"""
sfa_automation.py
The SFA download engine.

    one browser -> log in ONCE -> N tabs in parallel (same session) ->
    per tab: open URL -> fill filters -> Search -> Export (download) ->
             validate the file against the profile -> import into Chimera
    -> then build the daily reports from the fresh data

Design rules:
  * Each tab (report profile) succeeds or fails on its own, with its own
    retries; one failing report never stops the others.
  * Nothing is trusted blindly: every downloaded file is identified from
    its own headers and its banner is compared with the filters the
    profile asked for (Report Type, Invoice Status, dates...). A file that
    doesn't match is rejected, not imported.
  * Progress is published to LIVE (a thread-safe snapshot) so the UI can
    show one lane per tab while the run is going, and every run is
    recorded in automation_runs.
  * A cross-process lock means only one run can download at a time
    (manual, in-app schedule and Windows Task Scheduler included).

SalesPro is an Angular / PrimeNG single-page app, so controls are found by
the label text shown above them (exactly what the user types into a
profile), then driven by kind: PrimeNG dropdown / multiselect, ng-select,
native select, date input, text input or toggle.
"""

import asyncio
import base64
import json
import os
import re
import threading
import time
import traceback
import uuid
from datetime import date, datetime

import automation_store as AS
import sfa_config
import sfa_exports as X
import sfa_store

STEPS = ["login", "open", "filters", "search", "export", "validate", "import"]
STEP_LABELS = {"login": "Log in", "open": "Open", "filters": "Filters", "search": "Search",
               "export": "Export", "validate": "Validate", "import": "Import"}

DOWNLOAD_ROOT = os.path.join(os.path.expanduser("~"), "Chimera", "downloads")
SHOT_ROOT = os.path.join(os.path.expanduser("~"), "Chimera", "screenshots", "automation")

NO_DATA_PATTERNS = re.compile(r"no (records?|data|results?)( found| available)?|nothing to (show|display)", re.I)

# Selectors for the kinds of controls SalesPro (PrimeNG) and similar Angular apps use.
PANEL_SEL = ".p-dropdown-panel, .p-multiselect-panel, .ng-dropdown-panel, .p-overlay, .cdk-overlay-pane"
OPTION_SEL = "[role=option], li.p-dropdown-item, li.p-multiselect-item, .ng-option"


# Data saver: what SFA pages can skip without breaking (blocked inside Chrome
# itself, so the HTTP cache keeps working — Playwright request interception
# would disable the cache and use MORE data).
BLOCK_PATTERNS = ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.ico", "*.bmp",
                  "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot", "*.mp4", "*.webm", "*.mp3"]
BLOCK_TYPES = ("image", "font", "media")


class NetMeter:
    """
    Counts the internet data a run actually transfers (bytes on the wire,
    from Chrome's own network events) and applies the data saver to every
    tab, including tabs an export opens.
    """

    def __init__(self, data_saver=False):
        self.data_saver = bool(data_saver)
        self.bytes = 0
        self.blocked = 0
        self._seen = set()
        self._last_pub = 0

    def _add(self, n):
        self.bytes += int(n or 0)
        if self.bytes - self._last_pub > 50_000:
            self._last_pub = self.bytes
            LIVE.set(data_bytes=self.bytes, blocked=self.blocked)

    def add_download(self, url, path):
        """Chrome's network counters skip file downloads, so count the file itself
        (a report built in the page — blob:/data: — was already counted as the
        request that fetched its data)."""
        if str(url or "").startswith(("http:", "https:")):
            try:
                self._add(os.path.getsize(path))
            except OSError:
                pass

    async def attach(self, page):
        if id(page) in self._seen:
            return
        self._seen.add(id(page))
        try:  # Chrome / Edge: exact numbers via the DevTools protocol
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Network.enable")
            if self.data_saver:
                await cdp.send("Network.setBlockedURLs", {"urls": BLOCK_PATTERNS})
            cdp.on("Network.loadingFinished", lambda e: self._add(e.get("encodedDataLength", 0)))

            def failed(e):
                if e.get("blockedReason"):
                    self.blocked += 1
                else:
                    self._add(e.get("encodedDataLength", 0))
            cdp.on("Network.loadingFailed", failed)
        except Exception:  # Firefox: estimate from response sizes, block by resource type
            async def on_finished(req):
                try:
                    sz = await req.sizes()
                    self._add(sz.get("responseBodySize", 0) + sz.get("responseHeadersSize", 0)
                              + sz.get("requestHeadersSize", 0) + sz.get("requestBodySize", 0))
                except Exception:
                    pass
            page.on("requestfinished", lambda r: asyncio.ensure_future(on_finished(r)))
            if self.data_saver:
                async def route(r):
                    if r.request.resource_type in BLOCK_TYPES:
                        self.blocked += 1
                        await r.abort()
                    else:
                        await r.continue_()
                await page.route("**/*", route)

    def watch_popups(self, context):
        context.on("page", lambda p: asyncio.ensure_future(self.attach(p)))


class Cancelled(Exception):
    pass


class StepError(Exception):
    def __init__(self, step, message, retry=True):
        super().__init__(message)
        self.step, self.message, self.retry = step, message, retry


# ===========================================================================
# Live state (read by the UI every second)
# ===========================================================================
class LiveState:
    def __init__(self):
        self._lock = threading.Lock()
        self._s = {"active": False, "run_id": None, "lanes": [], "log": [], "phase": "idle"}
        self._cancel = threading.Event()

    def reset(self, **kw):
        with self._lock:
            self._cancel.clear()
            self._s = {"active": True, "run_id": None, "lanes": [], "log": [], "phase": "starting",
                       "started_at": datetime.now().isoformat(timespec="seconds"), "result": None, **kw}

    def set(self, **kw):
        with self._lock:
            self._s.update(kw)

    def lane(self, idx, **kw):
        with self._lock:
            if 0 <= idx < len(self._s["lanes"]):
                self._s["lanes"][idx].update(kw)

    def step(self, idx, step, state):
        with self._lock:
            if 0 <= idx < len(self._s["lanes"]):
                lane = self._s["lanes"][idx]
                lane["steps"][step] = state
                if state == "active":
                    lane["step"] = step

    def log(self, msg, lane=None, level="info"):
        with self._lock:
            self._s["log"].append({"t": datetime.now().strftime("%H:%M:%S"), "lane": lane, "level": level,
                                   "msg": msg})
            self._s["log"] = self._s["log"][-400:]

    def snapshot(self):
        with self._lock:
            s = json.loads(json.dumps(self._s, default=str))
        for lane in s.get("lanes", []):
            if lane.get("started") and lane.get("status") in ("running", "retrying"):
                lane["elapsed"] = round(time.time() - lane["started"])
        return s

    def cancel(self):
        self._cancel.set()
        self.log("Cancel requested — stopping after the current step.", level="warn")

    def cancelled(self):
        return self._cancel.is_set()


LIVE = LiveState()


def _new_lane(profile):
    return {"profile_id": profile["id"], "name": profile["name"], "feeds_into": profile["feeds_into"],
            "status": "queued", "step": None, "steps": {s: "pending" for s in STEPS}, "attempt": 0,
            "max_attempts": 1, "message": "", "rows": None, "file": None, "screenshot": None,
            "shots": [], "started": None, "elapsed": 0, "imported": []}


# ===========================================================================
# Browser helpers
# ===========================================================================
def _xq(s):
    return json.dumps(s)


async def _launch(p, browser_key, headless):
    channel = sfa_config.SUPPORTED_BROWSERS.get(browser_key)
    if browser_key == "firefox":
        return await p.firefox.launch(headless=headless)
    try:
        return await p.chromium.launch(headless=headless, channel=channel)
    except Exception as first:
        try:  # fall back to Playwright's own Chromium if the installed browser can't be driven
            return await p.chromium.launch(headless=headless)
        except Exception:
            raise RuntimeError(f"Could not start {browser_key}: {first}. If this keeps happening run "
                               "'py -m playwright install chromium'.")


async def _settle(page, timeout_ms=8000):
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass
    await page.wait_for_timeout(400)


async def _login(page, cfg, timeout_ms):
    resp = await page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=timeout_ms)
    try:  # compare the PC clock with SFA's on every login (scheduled runs depend on it)
        if resp:
            import scheduler
            scheduler.record_clock(resp.headers.get("date"), "SFA login")
    except Exception:
        pass
    pw = page.locator("input[type='password']").first
    try:
        await pw.wait_for(state="visible", timeout=15000)
    except Exception:
        await _settle(page)
        if await page.locator("input[type='password']").count() == 0:
            return True, "Already signed in."
        raise StepError("login", "The login form never appeared.")
    user = page.locator("input[name='username'], input[formcontrolname='username'], input[type='email'], "
                        "input[type='text']").first
    await user.fill(cfg["username"])
    await pw.fill(cfg["password"])
    await pw.press("Enter")
    try:
        await page.locator("input[type='password']").first.wait_for(state="hidden", timeout=25000)
    except Exception:
        raise StepError("login", "Still on the login page after submitting — check the SFA username and "
                                 "password in Settings.", retry=False)
    await _settle(page)
    return True, "Logged in."


async def _ensure_logged_in(page, cfg, timeout_ms):
    """If a tab landed on the login page (session expired), log in again in that tab."""
    if await page.locator("input[type='password']").count() and \
            await page.locator("input[type='password']").first.is_visible():
        await _login(page, cfg, timeout_ms)
        return True
    return False


_FIND_FIELD_JS = r"""
([label, token]) => {
  const norm = s => (s || '').replace(/[*:]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  const target = norm(label);
  const ctrlSel = '.p-dropdown, .p-multiselect, .ng-select, select, textarea, .p-inputswitch, ' +
                  'input:not([type=hidden]):not([type=button]):not([type=submit])';
  const insidePanel = el => !!el.closest('.p-dropdown-panel, .p-multiselect-panel, .ng-dropdown-panel, .p-overlay');
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const topLevel = ctrls => ctrls.filter(c => !ctrls.some(o => o !== c && o.contains(c)));
  const kindOf = c => {
    if (c.matches('.p-dropdown')) return 'dropdown';
    if (c.matches('.p-multiselect')) return 'multiselect';
    if (c.matches('.ng-select')) return c.matches('.ng-select-multiple') ? 'multiselect' : 'ngselect';
    if (c.matches('select')) return c.multiple ? 'nativemulti' : 'select';
    if (c.matches('.p-inputswitch') || (c.matches('input') && ['checkbox', 'radio'].includes(c.type))) return 'toggle';
    if (c.matches('input[type=date]')) return 'date';
    if (c.closest('.p-calendar')) return 'calendar';
    return 'text';
  };
  const labels = [...document.querySelectorAll('label, span, div, p, h5, h6, b, strong, td, th, mat-label, legend')]
    .filter(el => norm(el.textContent) === target && visible(el) && !insidePanel(el)
                  && !el.closest('.p-dropdown, .p-multiselect, .ng-select, select, nav, header')
                  && ![...el.children].some(ch => norm(ch.textContent) === target));
  for (const el of labels) {
    if (el.tagName === 'LABEL' && el.htmlFor) {
      const c = document.getElementById(el.htmlFor);
      if (c) { const top = c.closest('.p-dropdown, .p-multiselect, .ng-select, .p-inputswitch') || c;
               top.setAttribute('data-chimera', token); return {found: true, kind: kindOf(top)}; }
    }
    let a = el;
    for (let i = 0; i < 5 && a; i++) {
      a = a.parentElement;
      if (!a) break;
      const ctrls = topLevel([...a.querySelectorAll(ctrlSel)].filter(c => !insidePanel(c) && visible(c)));
      const after = ctrls.filter(c => el.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING);
      const pick = after[0] || null;
      if (pick) { pick.setAttribute('data-chimera', token); return {found: true, kind: kindOf(pick)}; }
    }
  }
  return {found: false};
}
"""

_PICK_OPTION_JS = r"""
([value, token, exactOnly]) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const panels = [...document.querySelectorAll('%PANEL%')].filter(visible);
  const opts = panels.flatMap(p => [...p.querySelectorAll('%OPTION%')]).filter(visible);
  const v = norm(value);
  let hit = opts.find(o => norm(o.getAttribute('aria-label')) === v || norm(o.textContent) === v);
  if (!hit && !exactOnly) {
    const partial = opts.filter(o => norm(o.textContent).includes(v));
    if (partial.length === 1) hit = partial[0];
  }
  if (!hit) return {found: false, options: opts.slice(0, 40).map(o => o.textContent.replace(/\s+/g, ' ').trim())};
  hit.scrollIntoView({block: 'center'});
  hit.setAttribute('data-chimera-opt', token);
  const selected = hit.getAttribute('aria-selected') === 'true' || hit.classList.contains('p-highlight')
                   || hit.classList.contains('ng-option-selected');
  return {found: true, selected};
}
""".replace("%PANEL%", PANEL_SEL).replace("%OPTION%", OPTION_SEL)


async def _find_field(page, label):
    token = uuid.uuid4().hex[:10]
    res = await page.evaluate(_FIND_FIELD_JS, [label, token])
    if not res.get("found"):
        return None, None
    return page.locator(f"[data-chimera='{token}']").first, res["kind"]


async def _open_panel(page, ctrl, kind):
    target = ctrl.locator(".ng-select-container").first if kind in ("ngselect", "multiselect") and \
        await ctrl.locator(".ng-select-container").count() else ctrl
    await target.click()
    try:
        await page.locator(PANEL_SEL).filter(has=page.locator(OPTION_SEL)).first.wait_for(state="visible",
                                                                                        timeout=8000)
    except Exception:
        await page.wait_for_timeout(500)


async def _type_in_panel_filter(page, ctrl, value):
    box = page.locator(".p-dropdown-filter:visible, .p-multiselect-filter:visible").first
    if await box.count():
        await box.fill(value)
        await page.wait_for_timeout(350)
        return True
    inner = ctrl.locator(".ng-select input").first if await ctrl.locator(".ng-select input").count() else \
        ctrl.locator("input[role=combobox], .ng-input input").first
    if await inner.count():
        await inner.fill(value)
        await page.wait_for_timeout(350)
        return True
    return False


async def _pick(page, value, exact_only=False):
    token = uuid.uuid4().hex[:10]
    res = await page.evaluate(_PICK_OPTION_JS, [value, token, exact_only])
    if not res.get("found"):
        return False, res.get("options", []), False
    if not res.get("selected"):
        await page.locator(f"[data-chimera-opt='{token}']").first.click()
    return True, [], res.get("selected", False)


async def _close_panel(page):
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(200)


async def _control_text(ctrl):
    try:
        return re.sub(r"\s+", " ", (await ctrl.inner_text()).strip())
    except Exception:
        return ""


async def fill_filter(page, f, report_date):
    """Fills one profile filter on the current page. Raises StepError with a clear reason."""
    label, ftype, value = f["label"], f["type"], f.get("value")
    if ftype != "date" and value in ("", None, []):
        return "left empty"
    ctrl, kind = await _find_field(page, label)
    if ctrl is None:
        raise StepError("filters", f"Couldn't find a filter labelled '{label}' on the page. Check the label "
                                   "spelling, or use 'Detect filters' to read the exact labels.")
    if ftype == "date":
        iso = AS.resolve_date(f, report_date)
        if kind == "date":
            await ctrl.fill(iso)
        else:
            ph = (await ctrl.get_attribute("placeholder") or "").lower()
            y, m, d = iso.split("-")
            text = f"{m}/{d}/{y}" if ph.startswith("mm") else (f"{d}/{m}/{y}" if ph.startswith("dd") else iso)
            await ctrl.click()
            await ctrl.fill(text)
            await page.keyboard.press("Escape")
        try:
            await ctrl.blur()
        except Exception:
            pass
        got = await ctrl.input_value() if kind in ("date", "calendar", "text") else ""
        if kind == "date" and got != iso:
            raise StepError("filters", f"Date filter '{label}' didn't take the value {iso} (shows '{got}').")
        return iso

    if ftype == "toggle":
        want = value == "on"
        is_on = await ctrl.evaluate(
            "c => c.matches('input') ? c.checked : (c.classList.contains('p-inputswitch-checked') "
            "|| c.getAttribute('aria-checked') === 'true' || !!c.querySelector('input:checked'))")
        if is_on != want:
            await ctrl.click()
            await page.wait_for_timeout(400)
        return value

    if ftype == "text" or kind == "text":
        if kind not in ("text", "calendar"):
            raise StepError("filters", f"'{label}' is a {kind} on the page, not a text box — change its type.",
                            retry=False)
        await ctrl.fill(str(value))
        return value

    if kind in ("select", "nativemulti"):
        vals = value if isinstance(value, list) else [value]
        try:
            await ctrl.select_option(label=vals if kind == "nativemulti" else vals[0])
        except Exception:
            raise StepError("filters", f"'{', '.join(vals)}' is not an option of '{label}'.", retry=False)
        return value

    if kind not in ("dropdown", "multiselect", "ngselect"):
        raise StepError("filters", f"'{label}' is a {kind} on the page; set its type accordingly.", retry=False)

    values = value if isinstance(value, list) else [value]
    if kind in ("dropdown", "ngselect") and len(values) > 1:
        raise StepError("filters", f"'{label}' accepts one value only.", retry=False)

    await _open_panel(page, ctrl, kind)
    for v in values:
        await _type_in_panel_filter(page, ctrl, v)
        ok, options, _already = await _pick(page, v, exact_only=True)
        if not ok:
            await _close_panel(page)
            hint = f" Options seen: {', '.join(options[:12])}" if options else ""
            raise StepError("filters", f"'{v}' is not an option of '{label}'.{hint}", retry=False)
        if kind == "multiselect":
            box = page.locator(".p-multiselect-filter:visible").first
            if await box.count():
                await box.fill("")
    if kind == "multiselect" or await page.locator(PANEL_SEL).filter(has=page.locator(OPTION_SEL)).count():
        await _close_panel(page)
    # SalesPro re-renders the control after a pick (the old element goes stale), so read
    # the result back from a fresh lookup by label rather than the handle we clicked.
    shown = ""
    for _ in range(10):
        await page.wait_for_timeout(250)
        fresh, _k = await _find_field(page, label)
        shown = (await _control_text(fresh)).lower() if fresh is not None else ""
        if all(v.lower() in shown for v in values):
            return value
    raise StepError("filters", f"Picked '{', '.join(values)}' for '{label}' but the box shows '{shown or 'nothing'}'.")


_CLICKABLES_JS = r"""
([want, token, exportMode]) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                        && getComputedStyle(el).visibility !== 'hidden';
  const sel = 'button, a, [role=button], .p-button, .btn, input[type=button], input[type=submit], ' +
              '[ptooltip], [mattooltip], [data-pr-tooltip], [title]';
  const els = [...document.querySelectorAll(sel)].filter(el => visible(el) && !el.closest('nav, header, .navbar, .p-menubar'));
  // keep the outermost clickable (a <button> rather than the <i> icon inside it)
  const top = els.filter(el => !els.some(o => o !== el && o.contains(el) && o.matches('button, a, [role=button]')));
  const info = top.map(el => {
    const icons = [...el.querySelectorAll('i, span, svg, img')].map(i => (i.getAttribute('class') || '') + ' ' + (i.getAttribute('src') || '') + ' ' + (i.getAttribute('data-icon') || '')).join(' ');
    const words = [el.innerText || el.value || '', el.getAttribute('title'), el.getAttribute('aria-label'),
                   el.getAttribute('ptooltip'), el.getAttribute('mattooltip'), el.getAttribute('data-pr-tooltip'),
                   el.getAttribute('data-original-title')].filter(Boolean).join(' | ');
    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true' || el.classList.contains('p-disabled');
    return {el, words: norm(words), icons: norm(icons + ' ' + (el.getAttribute('class') || '')), disabled};
  }).filter(x => !x.disabled);
  const w = norm(want);
  let hit = info.find(x => x.words === w) || info.find(x => w && x.words.includes(w));
  if (!hit && exportMode) {
    hit = info.find(x => /excel|xlsx?\b/.test(x.icons + ' ' + x.words))
       || info.find(x => /export|download/.test(x.icons + ' ' + x.words));
  }
  const seen = info.map(x => (x.words || ('[icon: ' + (x.icons.match(/(pi|fa|mdi|material-icons)[-\w ]*/) || [x.icons.slice(0, 40)])[0] + ']')).slice(0, 50))
                   .filter(Boolean).slice(0, 25);
  if (!hit) return {found: false, seen};
  hit.el.setAttribute('data-chimera-btn', token);
  return {found: true, label: hit.words || hit.icons.slice(0, 60), seen};
}
"""


async def _find_button(page, text, timeout_ms, export_mode=False):
    """
    Finds a clickable control by its text, tooltip/aria-label/title, or (for
    exports) by an Excel/export/download icon. Returns (locator | None,
    label it matched, [labels of buttons that ARE on the page]).
    """
    deadline = time.time() + timeout_ms / 1000
    seen = []
    while True:
        token = uuid.uuid4().hex[:10]
        res = await page.evaluate(_CLICKABLES_JS, [text, token, export_mode])
        seen = res.get("seen", [])
        if res.get("found"):
            return page.locator(f"[data-chimera-btn='{token}']").first, res.get("label", ""), seen
        if time.time() >= deadline:
            return None, "", seen
        await page.wait_for_timeout(500)


async def _click_by_text(page, text, timeout_ms):
    """Back-compatible helper: the matching button/link, or None."""
    el, _label, _seen = await _find_button(page, text, timeout_ms)
    return el


async def _click_and_download(page, el, timeout_ms):
    """
    Clicks and waits for a download from this tab OR from a new tab/popup the
    click opens (some systems export through window.open). Returns the
    Download or None.
    """
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def on_download(d):
        if not fut.done():
            fut.set_result(d)

    def on_page(p):
        p.on("download", on_download)

    page.on("download", on_download)
    page.context.on("page", on_page)
    try:
        await el.click()
        return await asyncio.wait_for(fut, timeout_ms / 1000)
    except asyncio.TimeoutError:
        return None
    finally:
        page.remove_listener("download", on_download)
        page.context.remove_listener("page", on_page)


async def _shot(page, lane_idx, name, folder, keep_b64=False):
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{lane_idx + 1}_{name}.jpg")
        data = await page.screenshot(type="jpeg", quality=60, full_page=False)
        with open(path, "wb") as f:
            f.write(data)
        LIVE.lane(lane_idx, screenshot=path)
        if keep_b64:
            with LIVE._lock:
                LIVE._s["lanes"][lane_idx]["shots"].append(
                    {"step": name, "img": base64.b64encode(data).decode("ascii")})
        return path
    except Exception:
        return None


# ===========================================================================
# Validation of a downloaded file against its profile
# ===========================================================================
_BANNER_ALIASES = {"date from": ["date from", "from date"], "date to": ["date to", "to date"],
                   "from date": ["from date", "date from"], "to date": ["to date", "date to"]}


def validate_download(path, profile, report_date):
    """
    Returns {"ok", "dataset", "rows", "empty", "checks": [...], "message"}.
    Checks: the file is the dataset the profile feeds, and its banner shows
    the same filter values the profile set (Report Type, dates, ...).
    """
    try:
        ex = X.read_export(path)
    except X.ExportError as e:
        return {"ok": False, "message": str(e), "checks": [], "rows": 0, "empty": False, "dataset": None}
    checks, ok = [], True
    feeds = profile["feeds_into"]
    if feeds != "download_only":
        good = ex["dataset"] == feeds
        checks.append({"what": "Report kind", "expect": AS.FEEDS[feeds], "got": ex["label"], "ok": good})
        ok &= good
    banner = {k.lower(): v for k, v in ex["banner"].items()}
    for c in AS.expected_banner_checks(profile, report_date):
        keys = _BANNER_ALIASES.get(c["key"].lower(), [c["key"].lower()])
        got = next((banner[k] for k in keys if k in banner), None)
        if got is None:
            continue  # this report's banner doesn't print that filter — nothing to compare
        if c.get("is_date"):
            good = X.to_iso_date(got) == c["expect"]
        else:
            good = got.strip().lower() == str(c["expect"]).strip().lower()
        checks.append({"what": c["key"], "expect": c["expect"], "got": got, "ok": good})
        ok &= good
    rows = len(ex["records"])
    # Files with no banner (Unproductive Visits): the rows' own dates must match.
    if ok and rows and not ex["banner"] and feeds == "unproductive":
        rows_norm, _ = X.normalise_unproductive(ex)
        dates = {r["visit_date"] for r in rows_norm}
        expect = next((AS.resolve_date(f, report_date) for f in profile["filters"] if f["type"] == "date"),
                      report_date)
        good = expect in dates
        checks.append({"what": "Visit dates", "expect": expect, "got": ", ".join(sorted(dates)[:3]), "ok": good})
        ok &= good
    msg = "File matches the profile." if ok else "Downloaded file doesn't match the profile: " + "; ".join(
        f"{c['what']} is '{c['got']}', expected '{c['expect']}'" for c in checks if not c["ok"])
    return {"ok": ok, "dataset": ex["dataset"], "rows": rows, "empty": rows == 0, "checks": checks,
            "message": msg, "generated_at": ex["generated_at"]}


# ===========================================================================
# One tab
# ===========================================================================
async def _run_lane(context, cfg, profile, idx, report_date, opts, sem):
    retries = opts["retries"]
    timeout_ms = opts["timeout_ms"]
    LIVE.lane(idx, max_attempts=retries + 1)
    async with sem:
        for attempt in range(1, retries + 2):
            if LIVE.cancelled():
                LIVE.lane(idx, status="cancelled", message="Cancelled before starting.")
                return
            LIVE.lane(idx, status="running", attempt=attempt, started=time.time(), message="")
            for s in STEPS[1:]:
                LIVE.step(idx, s, "pending")
            page = await context.new_page()
            if opts.get("meter"):
                await opts["meter"].attach(page)
            current = "open"
            try:
                # ---- open
                LIVE.step(idx, "open", "active")
                LIVE.log(f"Opening {profile['url']}", lane=profile["name"])
                await page.goto(profile["url"], wait_until="domcontentloaded", timeout=timeout_ms)
                await _settle(page)
                if await _ensure_logged_in(page, cfg, timeout_ms):
                    LIVE.log("Session had expired in this tab — logged in again.", lane=profile["name"])
                    await page.goto(profile["url"], wait_until="domcontentloaded", timeout=timeout_ms)
                    await _settle(page)
                first = next((f["label"] for f in profile["filters"]), None)
                if first:
                    deadline = time.time() + timeout_ms / 1000
                    while (await _find_field(page, first))[0] is None:
                        if time.time() > deadline:
                            raise StepError("open", f"The page loaded but the '{first}' filter never appeared. "
                                                    "Check the URL and the label spelling (Detect filters shows "
                                                    "the exact labels).")
                        if LIVE.cancelled():
                            raise Cancelled()
                        await page.wait_for_timeout(500)
                LIVE.step(idx, "open", "done")
                if opts["test"]:
                    await _shot(page, idx, "open", opts["shot_dir"], keep_b64=True)

                # ---- filters
                current = "filters"
                LIVE.step(idx, "filters", "active")
                for f in profile["filters"]:
                    if LIVE.cancelled():
                        raise Cancelled()
                    v = await fill_filter(page, f, report_date)
                    LIVE.log(f"{f['label']} = {v if not isinstance(v, list) else ', '.join(v)}",
                             lane=profile["name"])
                LIVE.step(idx, "filters", "done")
                if opts["test"]:
                    await _shot(page, idx, "filters", opts["shot_dir"], keep_b64=True)

                # ---- search
                current = "search"
                LIVE.step(idx, "search", "active")
                btn, _lbl, seen = await _find_button(page, profile["search_text"], 10000)
                if btn is None:
                    raise StepError("search", f"No '{profile['search_text']}' button found. Buttons on the page: "
                                              + ("; ".join(seen[:15]) or "none"), retry=False)
                await btn.click()
                await _settle(page, profile["wait_seconds"] * 1000)
                LIVE.step(idx, "search", "done")
                if opts["test"]:
                    await _shot(page, idx, "search", opts["shot_dir"], keep_b64=True)

                # ---- export (download)
                current = "export"
                LIVE.step(idx, "export", "active")
                parts = [p.strip() for p in profile["export_text"].split(">") if p.strip()]
                wait_ms = profile["wait_seconds"] * 1000
                for p_text in parts[:-1]:  # e.g. "Export > Excel": open the menu first
                    el, _lbl, seen = await _find_button(page, p_text, wait_ms, export_mode=True)
                    if el is None:
                        raise StepError("export", f"No '{p_text}' button found. Buttons on the page: "
                                                  + "; ".join(seen[:15]))
                    await el.click()
                    await page.wait_for_timeout(500)
                el, matched, seen = await _find_button(page, parts[-1], wait_ms, export_mode=True)
                if el is not None and matched and parts[-1].lower() not in matched:
                    LIVE.log(f"No button says '{parts[-1]}' — using the one that looks like an export: "
                             f"'{matched[:50]}'.", lane=profile["name"], level="warn")
                if el is None:
                    body = await page.inner_text("body")
                    if NO_DATA_PATTERNS.search(body) and profile["allow_empty"]:
                        LIVE.step(idx, "export", "skipped")
                        LIVE.step(idx, "validate", "skipped")
                        LIVE.step(idx, "import", "active")
                        if opts["import"] and profile["feeds_into"] != "download_only":
                            sfa_store.record_empty_dataset(profile["feeds_into"], report_date,
                                                           note="SFA showed no records for this date.")
                        LIVE.step(idx, "import", "done")
                        LIVE.lane(idx, status="empty", rows=0, message="SFA has no records for this date.")
                        LIVE.log("No records in SFA for this date.", lane=profile["name"])
                        return
                    raise StepError("export", f"No '{parts[-1]}' button appeared within "
                                              f"{profile['wait_seconds']}s after searching. Buttons Chimera can "
                                              "see: " + ("; ".join(seen[:15]) or "none") + ". Set the profile's "
                                              "Export button text to one of these (or raise the wait time if "
                                              "SFA is still loading).")
                os.makedirs(opts["download_dir"], exist_ok=True)
                dl = await _click_and_download(page, el, wait_ms)
                if dl is None:
                    raise StepError("export", f"Clicked '{(matched or parts[-1])[:50]}' but no file downloaded within "
                                              f"{profile['wait_seconds']}s. If it opens a menu, set the export text "
                                              "to e.g. 'Export > Excel'; for big reports raise the wait time.")
                fail = await dl.failure()
                if fail:
                    raise StepError("export", f"Download failed: {fail}")
                safe = "".join(c for c in profile["name"] if c.isalnum() or c in " _-").strip() or "report"
                ext = os.path.splitext(dl.suggested_filename or "")[1] or ".xlsx"
                path = os.path.join(opts["download_dir"], f"{idx + 1:02d} {safe}{ext}")
                await dl.save_as(path)
                if opts.get("meter"):
                    opts["meter"].add_download(dl.url, path)
                LIVE.lane(idx, file=path)
                LIVE.step(idx, "export", "done")
                LIVE.log(f"Downloaded {os.path.basename(path)}", lane=profile["name"])

                # ---- validate
                current = "validate"
                LIVE.step(idx, "validate", "active")
                v = validate_download(path, profile, report_date)
                LIVE.lane(idx, rows=v["rows"], checks=v["checks"])
                if not v["ok"]:
                    # Downloading again gives the same file, so a mismatch isn't retried.
                    raise StepError("validate", v["message"], retry=False)
                LIVE.step(idx, "validate", "done")

                # ---- import
                current = "import"
                LIVE.step(idx, "import", "active")
                if not opts["import"] or profile["feeds_into"] == "download_only":
                    LIVE.step(idx, "import", "skipped")
                    LIVE.lane(idx, status="done", message="Downloaded and checked" +
                              (" (test run — not imported)." if opts["test"] else "."))
                    return
                if v["empty"]:
                    if not profile["allow_empty"]:
                        raise StepError("import", "The export has no rows and this profile doesn't allow "
                                                  "empty reports.", retry=False)
                    sfa_store.record_empty_dataset(profile["feeds_into"], report_date, source_file=path,
                                                   generated_at=v.get("generated_at", ""))
                    LIVE.step(idx, "import", "done")
                    LIVE.lane(idx, status="empty", rows=0, message="Export had no rows for this date.")
                    return
                res = sfa_store.import_export_file(path)
                LIVE.step(idx, "import", "done")
                LIVE.lane(idx, status="done", imported=res["stored"],
                          message=", ".join(f"{s['rows']} rows for {s['date']}" for s in res["stored"]))
                LIVE.log("Imported " + ", ".join(f"{s['rows']} rows" for s in res["stored"]), lane=profile["name"])
                return

            except Cancelled:
                LIVE.step(idx, current, "failed")
                LIVE.lane(idx, status="cancelled", message="Cancelled.")
                return
            except StepError as e:
                if LIVE.cancelled():
                    LIVE.step(idx, e.step, "failed")
                    LIVE.lane(idx, status="cancelled", message="Cancelled.")
                    return
                LIVE.step(idx, e.step, "failed")
                shot = await _shot(page, idx, f"fail_{e.step}_try{attempt}", opts["shot_dir"], keep_b64=opts["test"])
                last = attempt >= retries + 1 or not e.retry
                LIVE.lane(idx, status="failed" if last else "retrying", message=e.message, screenshot=shot)
                LIVE.log(f"{STEP_LABELS[e.step]} failed: {e.message}" + ("" if last else " — retrying"),
                         lane=profile["name"], level="error" if last else "warn")
                if last:
                    return
            except Exception as e:  # Playwright timeouts, navigation errors, ...
                LIVE.step(idx, current, "failed")
                if LIVE.cancelled():
                    LIVE.lane(idx, status="cancelled", message="Cancelled.")
                    return
                shot = await _shot(page, idx, f"fail_{current}_try{attempt}", opts["shot_dir"], keep_b64=opts["test"])
                msg = str(e).split("\n")[0][:300]
                if 'waiting for event "download"' in msg:
                    msg = (f"Clicked '{profile['export_text']}' but no file downloaded within "
                           f"{profile['wait_seconds']}s. If Export opens a menu, set the export text to "
                           "e.g. 'Export > Excel'; for big reports raise the wait time.")
                elif "Timeout" in msg and current == "open":
                    msg = "SFA took too long to open the report page."
                last = attempt >= retries + 1
                LIVE.lane(idx, status="failed" if last else "retrying", message=msg, screenshot=shot)
                LIVE.log(f"{STEP_LABELS.get(current, current)} error: {msg}" + ("" if last else " — retrying"),
                         lane=profile["name"], level="error" if last else "warn")
                if last:
                    return
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
            await asyncio.sleep(2)


# ===========================================================================
# Whole run
# ===========================================================================
async def _run_async(cfg, items, report_date, opts):
    """items: [(lane_index, profile)] — each tab updates its own fixed lane in LIVE."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        LIVE.set(phase="logging_in")
        LIVE.log(f"Starting {cfg.get('browser', 'chrome')} ({'hidden' if opts['headless'] else 'visible'}).")
        browser = await _launch(p, cfg.get("browser", "chrome"), opts["headless"])
        try:
            context = await browser.new_context(accept_downloads=True, viewport={"width": 1500, "height": 950})
            meter = opts["meter"]
            meter.watch_popups(context)
            page = await context.new_page()
            await meter.attach(page)
            if meter.data_saver:
                LIVE.log("Data saver is on — SFA images and fonts are not downloaded.")
            for i, _p in items:
                LIVE.step(i, "login", "active")
            try:
                ok, msg = await _login(page, cfg, opts["timeout_ms"])
            except StepError as e:
                await _shot(page, items[0][0], "login_failed", opts["shot_dir"], keep_b64=opts["test"])
                for i, _p in items:
                    LIVE.step(i, "login", "failed")
                    LIVE.lane(i, status="failed", message=e.message)
                LIVE.log(e.message, level="error")
                return False
            except Exception as e:
                await _shot(page, items[0][0], "login_failed", opts["shot_dir"], keep_b64=opts["test"])
                for i, _p in items:
                    LIVE.step(i, "login", "failed")
                    LIVE.lane(i, status="failed", message=f"Couldn't reach SFA: {str(e).splitlines()[0]}")
                LIVE.log(f"Couldn't reach SFA: {e}", level="error")
                return False
            LIVE.log(msg)
            for i, _p in items:
                LIVE.step(i, "login", "done")
            await page.close()
            LIVE.set(phase="downloading")
            sem = asyncio.Semaphore(opts["parallel"])
            await asyncio.gather(*[_run_lane(context, cfg, prof, i, report_date, opts, sem)
                                   for i, prof in items])
            return True
        finally:
            await browser.close()


def run_automation(report_date=None, profile_ids=None, trigger="manual", slot=None, headless=None,
                   generate=True, import_data=True, test=False):
    """
    Synchronous entry point (call it from a background thread for the UI).
    Returns a summary dict; live progress is in LIVE.snapshot().
    """
    report_date = report_date or date.today().isoformat()
    settings = AS.get_automation_settings()
    owner = f"{trigger} run ({datetime.now():%H:%M})"
    if not AS.acquire_lock(owner):
        holder = AS.lock_holder() or {}
        msg = f"Another SFA run is already in progress ({holder.get('owner', 'unknown')})."
        LIVE.set(active=False, phase="done", result={"ok": False, "message": msg})
        return {"ok": False, "status": "busy", "message": msg}

    run_id = None
    try:
        all_profiles = AS.list_profiles()
        profiles = [p for p in all_profiles if (p["id"] in profile_ids if profile_ids else p["enabled"])]
        LIVE.reset(trigger=trigger, report_date=report_date, slot=slot, test=test)
        LIVE.set(lanes=[_new_lane(p) for p in profiles])
        run_id = AS.start_run_record(report_date, trigger, slot)
        LIVE.set(run_id=run_id)

        cfg = sfa_config.load_config()
        problem = None
        if not profiles:
            problem = "No report profiles are enabled."
        elif not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
            problem = "SFA login isn't set up — enter the login URL, username and password in Settings."
        if problem:
            for i in range(len(profiles)):
                LIVE.lane(i, status="failed", message=problem)
            LIVE.log(problem, level="error")
            AS.finish_run_record(run_id, "failed", LIVE.snapshot()["lanes"], message=problem)
            LIVE.set(active=False, phase="done", result={"ok": False, "message": problem})
            return {"ok": False, "status": "failed", "message": problem, "run_id": run_id}

        runnable = []
        for i, p in enumerate(profiles):
            if p["problems"]:
                LIVE.lane(i, status="failed", message=" ".join(p["problems"]))
                for s in STEPS:
                    LIVE.step(i, s, "skipped")
                LIVE.log(f"Skipped — {' '.join(p['problems'])}", lane=p["name"], level="error")
            else:
                runnable.append(i)

        stamp = datetime.now().strftime("%H%M%S")
        opts = {
            "retries": 0 if test else int(settings["retries"]),
            "parallel": int(settings["parallel_tabs"]),
            "timeout_ms": int(settings["page_timeout_seconds"]) * 1000,
            "headless": (settings["headless"] == "1") if headless is None else bool(headless),
            "download_dir": os.path.join(DOWNLOAD_ROOT, report_date, stamp),
            "shot_dir": os.path.join(SHOT_ROOT, report_date, stamp),
            "import": import_data and not test,
            "test": test,
            "meter": NetMeter(settings.get("data_saver") == "1"),
        }
        LIVE.set(data_bytes=0, blocked=0, data_saver=opts["meter"].data_saver)
        if runnable:
            # Profiles with problems keep their (skipped) lanes; the rest run in their own lanes.
            asyncio.run(_run_async(cfg, [(i, profiles[i]) for i in runnable], report_date, opts))

        lanes = LIVE.snapshot()["lanes"]
        ok_lanes = [l for l in lanes if l["status"] in ("done", "empty")]
        cancelled = LIVE.cancelled()
        report_run = None
        if generate and not test and ok_lanes and not cancelled:
            kinds = settings["generate_list"]
            if kinds:
                LIVE.set(phase="generating")
                LIVE.log("Building reports: " + ", ".join(kinds))
                import report_pipeline
                failed = [l["name"] for l in lanes if l["status"] not in ("done", "empty")]
                report_run = report_pipeline.run_reports(report_date, kinds=kinds, trigger=trigger)
                for r in report_run["reports"]:
                    r.pop("trace", None)
                    LIVE.log(f"{r['label']}: {'OK' if r['ok'] else r.get('reason')}",
                             level="info" if r["ok"] else "error")
                if failed:
                    LIVE.log("Reports used the earlier SFA data for: " + ", ".join(failed)
                             + " (their download failed this run).", level="warn")
                if report_run.get("status") == "pending_review":
                    LIVE.log("Reports are waiting for your approval (Preview before saving is on). "
                             "Open the bell or Daily SFA Reports to review them.", level="warn")
                elif report_run.get("ok") and settings.get("wa_auto_send") == "1":
                    import whatsapp_sender
                    if [r for r in whatsapp_sender.list_rules() if r["enabled"]]:
                        LIVE.set(phase="sending")
                        LIVE.log("Sending reports to WhatsApp…")
                        wa = whatsapp_sender.deliver_run(report_run["run_id"], wait=True)
                        LIVE.log("WhatsApp: " + wa.get("message", ""), level="info" if wa.get("ok") else "error")
                        report_run["whatsapp"] = wa
        if cancelled:
            status = "cancelled"
        elif len(ok_lanes) == len(lanes) and lanes:
            status = "success"
        elif ok_lanes:
            status = "partial"
        else:
            status = "failed"
        meter = opts["meter"]
        LIVE.set(data_bytes=meter.bytes, blocked=meter.blocked)
        msg = f"{len(ok_lanes)} of {len(lanes)} report(s) downloaded."
        if runnable:
            LIVE.log(f"Internet data used by this run: {meter.bytes / 1_048_576:.2f} MB"
                     + (f" ({meter.blocked} images/fonts skipped by data saver)" if meter.data_saver else "") + ".")
        AS.finish_run_record(run_id, status, [{k: v for k, v in l.items() if k != "shots"} for l in lanes],
                             report_run.get("run_id") if report_run else None, msg,
                             data_bytes=meter.bytes if runnable else None, blocked=meter.blocked,
                             data_saver=meter.data_saver)
        result = {"ok": status in ("success", "partial"), "status": status, "message": msg, "run_id": run_id,
                  "report_run": report_run}
        LIVE.set(active=False, phase="done", result=result)
        LIVE.log(f"Run finished: {status.upper()} — {msg}", level="info" if status == "success" else "warn")
        return result
    except Exception as e:
        msg = f"Unexpected error: {e}"
        LIVE.log(msg + "\n" + traceback.format_exc(limit=3), level="error")
        if run_id:
            AS.finish_run_record(run_id, "failed", LIVE.snapshot()["lanes"], message=msg)
        LIVE.set(active=False, phase="done", result={"ok": False, "message": msg})
        return {"ok": False, "status": "failed", "message": msg, "run_id": run_id}
    finally:
        AS.release_lock()


def start_background(**kw):
    """Starts a run in a daemon thread. Returns immediately."""
    if LIVE.snapshot().get("active"):
        return {"ok": False, "message": "A run is already in progress."}
    LIVE.reset(trigger=kw.get("trigger", "manual"), report_date=kw.get("report_date"))
    t = threading.Thread(target=run_automation, kwargs=kw, daemon=True, name="sfa-automation")
    t.start()
    return {"ok": True, "message": "Run started."}


# ===========================================================================
# Detect filters
# ===========================================================================
_DETECT_JS = r"""
() => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const insidePanel = el => !!el.closest('.p-dropdown-panel, .p-multiselect-panel, .ng-dropdown-panel, .p-overlay');
  const ctrlSel = '.p-dropdown, .p-multiselect, .ng-select, select, textarea, .p-inputswitch, ' +
                  'input:not([type=hidden]):not([type=button]):not([type=submit])';
  const all = [...document.querySelectorAll(ctrlSel)].filter(c => visible(c) && !insidePanel(c)
                && !c.closest('nav, header, .navbar, .p-menubar'));
  const top = all.filter(c => !all.some(o => o !== c && o.contains(c)));
  const kindOf = c => {
    if (c.matches('.p-dropdown')) return 'dropdown';
    if (c.matches('.p-multiselect')) return 'multiselect';
    if (c.matches('.ng-select')) return c.matches('.ng-select-multiple') ? 'multiselect' : 'dropdown';
    if (c.matches('select')) return c.multiple ? 'multiselect' : 'dropdown';
    if (c.matches('.p-inputswitch') || (c.matches('input') && ['checkbox', 'radio'].includes(c.type))) return 'toggle';
    if (c.matches('input[type=date]') || c.closest('.p-calendar')) return 'date';
    return 'text';
  };
  const textOf = el => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const out = [];
  top.forEach((c, i) => {
    let label = '';
    if (c.id) { const l = document.querySelector(`label[for="${c.id}"]`); if (l) label = textOf(l); }
    let a = c;
    for (let d = 0; d < 4 && !label && a; d++) {
      a = a.parentElement;
      if (!a) break;
      const walker = document.createTreeWalker(a, NodeFilter.SHOW_TEXT);
      let n, best = '';
      while ((n = walker.nextNode())) {
        const el = n.parentElement;
        if (!el || c.contains(el) || insidePanel(el)) continue;
        if (!(el.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        const t = n.textContent.replace(/\s+/g, ' ').trim();
        if (t && t.length <= 40) best = t;   // the closest text before the control
      }
      if (best) label = best;
    }
    label = label.replace(/[*:]+$/, '').trim();
    let state = null;
    if (kindOf(c) === 'toggle') state = c.matches('input') ? c.checked :
        (c.classList.contains('p-inputswitch-checked') || !!c.querySelector('input:checked'));
    c.setAttribute('data-chimera-detect', String(i));
    out.push({idx: i, label, type: kindOf(c), toggled: state,
              native_options: c.matches('select') ? [...c.options].map(o => o.textContent.trim()).filter(Boolean) : null});
  });
  return out;
}
"""


async def _detect_async(cfg, url, headless, read_options):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await _launch(p, cfg.get("browser", "chrome"), headless)
        try:
            context = await browser.new_context(viewport={"width": 1500, "height": 950})
            page = await context.new_page()
            await NetMeter(AS.get_automation_settings().get("data_saver") == "1").attach(page)
            await _login(page, cfg, 60000)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await _settle(page, 15000)
            if await _ensure_logged_in(page, cfg, 60000):
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await _settle(page, 15000)
            fields = []
            for _ in range(20):  # the SPA may render the filters a moment after load
                fields = await page.evaluate(_DETECT_JS)
                if fields:
                    break
                await page.wait_for_timeout(750)
            for f in fields:
                f["options"] = f.pop("native_options") or []
                if read_options and f["type"] in ("dropdown", "multiselect") and not f["options"]:
                    ctrl = page.locator(f"[data-chimera-detect='{f['idx']}']").first
                    try:
                        await ctrl.click()
                        await page.locator(PANEL_SEL).filter(has=page.locator(OPTION_SEL)).first.wait_for(
                            state="visible", timeout=4000)
                        opts = await page.evaluate(
                            "([ps, os]) => [...document.querySelectorAll(ps)].filter(p => p.offsetParent !== null)"
                            ".flatMap(p => [...p.querySelectorAll(os)]).map(o => o.textContent.replace(/\\s+/g,' ')"
                            ".trim()).filter(Boolean).slice(0, 200)", [PANEL_SEL, OPTION_SEL])
                        f["options"] = opts
                    except Exception:
                        f["options"] = []
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(200)
            shot = await page.screenshot(type="jpeg", quality=55)
            return fields, base64.b64encode(shot).decode("ascii"), await page.title()
        finally:
            await browser.close()


def detect_filters(url, headless=True, read_options=True):
    """Opens the report page and lists its filters (label, type, options). Never changes anything in SFA."""
    cfg = sfa_config.load_config()
    if not (cfg.get("login_url") and cfg.get("username") and cfg.get("password")):
        return {"ok": False, "message": "SFA login isn't set up — fill it in under Settings first."}
    if not re.match(r"^https?://", url or ""):
        return {"ok": False, "message": "Enter the report's URL first (starting with https://)."}
    try:
        fields, shot, title = asyncio.run(_detect_async(cfg, url, headless, read_options))
    except StepError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:
        return {"ok": False, "message": f"Couldn't read the page: {str(e).splitlines()[0]}"}
    fields = [f for f in fields if f["label"]]
    if not fields:
        return {"ok": False, "message": "No filters found on that page — is the URL right, and are filters "
                                        "shown (Show Filters switched on)?", "screenshot": shot}
    return {"ok": True, "fields": fields, "screenshot": shot, "title": title,
            "message": f"Found {len(fields)} filter(s)."}
