"""
whatsapp_sender.py
Sends the finished report images to WhatsApp chats/groups through
WhatsApp Web, using the user's own WhatsApp account.

How it works
  * Chimera keeps its own browser profile (~/Chimera/whatsapp_profile), so
    WhatsApp Web stays linked after the user scans the QR code ONCE
    ("Link WhatsApp"). Nothing else on the PC is touched.
  * Delivery rules say which chat gets which reports, for which manager
    groups, with an optional caption. One send opens WhatsApp Web, and for
    each rule: search the chat -> open it -> attach the images -> caption ->
    send -> wait until WhatsApp shows the message left the phone (no clock
    icon). Every send is logged.

Caveats (told to the user in the UI):
  * WhatsApp Web's page changes from time to time; selectors below try
    several known variants and every failure saves a screenshot.
  * WhatsApp does not officially support automated sending from personal
    accounts. Sending a few report messages to your own groups a few times
    a day is low-risk, but bulk/spam-like use can get a number restricted.
"""

import asyncio
import uuid
import json
import os
import re
import threading
import time
from datetime import datetime

import automation_store as AS
import db
import report_pipeline

WA_URL = os.environ.get("CHIMERA_WA_URL", "https://web.whatsapp.com/")
PROFILE_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "whatsapp_profile")
SHOT_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "screenshots", "whatsapp")
BATCH = 10   # images per message (WhatsApp allows up to 30)

SEL_READY = "#pane-side, div[aria-label='Chat list'], div[aria-label='Chats']"
SEL_QR = "canvas[aria-label*='Scan' i], div[data-ref] canvas, [data-testid='qrcode']"
SEL_SEARCH = ("#side div[contenteditable='true'][role='textbox'], div[aria-label='Search input textbox'], "
              "#side input[role='textbox'], div[contenteditable='true'][data-tab='3']")
SEL_COMPOSE = "footer div[contenteditable='true'][role='textbox'], #main footer div[contenteditable='true']"
SEL_ATTACH = ("#main [aria-label='Attach'], #main [title='Attach'], #main span[data-icon='plus-rounded'], "
              "#main span[data-icon='plus'], #main span[data-icon='attach-menu-plus'], #main span[data-icon='clip']")
SEL_IMG_INPUT = "input[type='file'][accept*='image']"
SEL_CAPTION = ("div[contenteditable='true'][aria-label*='caption' i], "
               "div[contenteditable='true'][aria-placeholder*='caption' i]")
SEL_SEND = ("[aria-label='Send'], span[data-icon='send'], span[data-icon='wds-ic-send-filled'], "
            "div[role='button'][aria-label='Send']")
SEL_PENDING = "#main span[data-icon='msg-time']"


class WAError(Exception):
    pass


# ===========================================================================
# Storage: rules + deliveries
# ===========================================================================
def init_schema():
    conn = db.get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wa_rules (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_name TEXT NOT NULL,
            kinds     TEXT NOT NULL DEFAULT 'sku,pivot,hourly',
            groups    TEXT NOT NULL DEFAULT '["*"]',
            caption   TEXT NOT NULL DEFAULT '{report} - {date}',
            enabled   INTEGER NOT NULL DEFAULT 1
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wa_deliveries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            report_run_id INTEGER,
            chat_name     TEXT NOT NULL,
            files         INTEGER NOT NULL,
            status        TEXT NOT NULL,       -- sent | failed | skipped
            message       TEXT,
            sent_at       TEXT NOT NULL
        )""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wa_rules)")}
    if "send_as" not in cols:      # images | pdf | both
        conn.execute("ALTER TABLE wa_rules ADD COLUMN send_as TEXT NOT NULL DEFAULT 'images'")
    if "orientation" not in cols:  # portrait (wide pages turned upright) | landscape (pages as they are)
        conn.execute("ALTER TABLE wa_rules ADD COLUMN orientation TEXT NOT NULL DEFAULT 'portrait'")
    conn.commit()
    conn.close()


def list_rules():
    conn = db.get_connection()
    rows = conn.execute("SELECT id, chat_name, kinds, groups, caption, enabled, send_as, orientation "
                        "FROM wa_rules ORDER BY id").fetchall()
    conn.close()
    return [{"id": r[0], "chat_name": r[1], "kinds": [k for k in r[2].split(",") if k],
             "groups": json.loads(r[3] or '["*"]'), "caption": r[4], "enabled": bool(r[5]),
             "send_as": r[6] or "images", "orientation": r[7] or "portrait"} for r in rows]


def save_rule(data):
    chat = str(data.get("chat_name", "")).strip()
    if not chat:
        return False, "Choose or type the chat / group name."
    kinds = [k for k in (data.get("kinds") or []) if k in report_pipeline.REPORT_KINDS]
    if not kinds:
        return False, "Tick at least one report to send."
    groups = data.get("groups") or ["*"]
    if not isinstance(groups, list) or not groups:
        groups = ["*"]
    caption = str(data.get("caption", "")).strip()[:500]
    send_as = data.get("send_as") or "images"
    if send_as not in ("images", "pdf", "both"):
        return False, "Send as must be photos, PDF documents or both."
    orientation = data.get("orientation") or "portrait"
    if orientation not in ("portrait", "landscape"):
        return False, "Orientation must be portrait or landscape."
    conn = db.get_connection()
    try:
        vals = (chat, ",".join(kinds), json.dumps(groups), caption, 1 if data.get("enabled", True) else 0, send_as,
                orientation)
        if data.get("id"):
            n = conn.execute("UPDATE wa_rules SET chat_name=?, kinds=?, groups=?, caption=?, enabled=?, send_as=?, "
                             "orientation=? WHERE id=?", vals + (data["id"],)).rowcount
            if not n:
                return False, "That rule no longer exists."
        else:
            conn.execute("INSERT INTO wa_rules (chat_name, kinds, groups, caption, enabled, send_as, orientation) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", vals)
        conn.commit()
    finally:
        conn.close()
    return True, "Delivery rule saved."


def delete_rule(rid):
    conn = db.get_connection()
    conn.execute("DELETE FROM wa_rules WHERE id=?", (rid,))
    conn.commit()
    conn.close()


def _log_delivery(run_id, chat, files, status, message):
    conn = db.get_connection()
    conn.execute("""INSERT INTO wa_deliveries (report_run_id, chat_name, files, status, message, sent_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 (run_id, chat, files, status, message, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()


def list_deliveries(limit=30):
    conn = db.get_connection()
    rows = conn.execute("""SELECT id, report_run_id, chat_name, files, status, message, sent_at
                           FROM wa_deliveries ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    keys = ["id", "report_run_id", "chat_name", "files", "status", "message", "sent_at"]
    return [dict(zip(keys, r)) for r in rows]


KIND_ORDER = ("sku", "pivot", "hourly")


def _landscape_copy(path):
    """The page as it was drawn (wide), from the portrait-turned image: made once, kept next to it."""
    folder = os.path.join(os.path.dirname(path), "landscape")
    out = os.path.join(folder, os.path.basename(path))
    if not os.path.exists(out):
        from PIL import Image
        os.makedirs(folder, exist_ok=True)
        with Image.open(path) as im:
            im.rotate(-90, expand=True).save(out, optimize=True)
    return out


def _caption(rule, run, label):
    return (rule["caption"] or "").replace("{date}", run["report_date"]).replace("{report}", label) \
        .replace("{time}", datetime.now().strftime("%H:%M"))


def plan_delivery(run, rules=None):
    """
    What goes to which chat for this report run. Images are sent report by
    report — SKU, then Pivot, then Hourly — each as its own message with its
    own caption; then the PDFs. Portrait or landscape per chat.
    """
    s = AS.get_automation_settings()
    styles_built = {o["style"] for rep in run["summary"].get("reports", []) for o in rep.get("outputs", [])}
    style = s["wa_style"] if s["wa_style"] in styles_built else (sorted(styles_built)[0] if styles_built else None)
    items = report_pipeline.image_items(run, style)
    plan = []
    for rule in (rules if rules is not None else [r for r in list_rules() if r["enabled"]]):
        send_as = rule.get("send_as", "images")
        landscape = rule.get("orientation", "portrait") == "landscape"
        batches = []
        if send_as in ("images", "both"):
            for kind in KIND_ORDER:
                chosen = [it for it in items if it["kind"] == kind and kind in rule["kinds"]
                          and ("*" in rule["groups"] or it["group"] == "*" or it["group"] in rule["groups"])]
                if not chosen:
                    continue
                files = []
                for it in chosen:
                    p = it["path"]
                    if landscape and it.get("rotated") and os.path.exists(p):
                        try:
                            p = _landscape_copy(p)
                        except Exception:
                            pass
                    files.append(p)
                batches.append({"kind": kind, "label": chosen[0]["label"], "files": files,
                                "pages": [it["page"] for it in chosen],
                                "caption": _caption(rule, run, chosen[0]["label"])})
        pdfs = []
        if send_as in ("pdf", "both"):    # whole report PDFs (all managers) for the ticked reports, in the sent style
            reps = sorted([r for r in run["summary"].get("reports", []) if r.get("ok") and r["kind"] in rule["kinds"]],
                          key=lambda r: KIND_ORDER.index(r["kind"]) if r["kind"] in KIND_ORDER else 9)
            for rep in reps:
                for o in rep.get("outputs") or [{"style": style, "pdf": rep.get("pdf")}]:
                    if o.get("pdf") and (o.get("style") in (style, None)):
                        pdfs.append(o["pdf"])
        labels = ", ".join(b["label"] for b in batches) or ", ".join(
            r["label"] for r in run["summary"].get("reports", []) if r.get("ok") and r["kind"] in rule["kinds"])
        plan.append({"rule_id": rule["id"], "chat_name": rule["chat_name"], "batches": batches,
                     "files": [f for b in batches for f in b["files"]], "pdfs": pdfs, "send_as": send_as,
                     "orientation": "landscape" if landscape else "portrait",
                     "pages": [pg for b in batches for pg in b["pages"]], "caption": _caption(rule, run, labels)})
    return plan


# ===========================================================================
# Live state for the UI
# ===========================================================================
class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.s = {"active": False, "action": None, "log": [], "result": None}

    def start(self, action):
        with self.lock:
            if self.s["active"]:
                return False
            self.s = {"active": True, "action": action, "log": [], "result": None,
                      "started_at": datetime.now().isoformat(timespec="seconds")}
            return True

    def log(self, msg, level="info"):
        with self.lock:
            self.s["log"].append({"t": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level})
            self.s["log"] = self.s["log"][-200:]

    def finish(self, result):
        with self.lock:
            self.s["active"] = False
            self.s["result"] = result

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.s, default=str))


WA = _State()
_ACTIVE = {"loop": None, "ctx": None}


def force_stop(timeout=10):
    """Close the WhatsApp Web window of a send / link in progress, immediately."""
    loop, ctx = _ACTIVE["loop"], _ACTIVE["ctx"]
    if not (loop and ctx):
        return False
    WA.log("Stopped immediately by the user.", level="warn")
    try:
        asyncio.run_coroutine_threadsafe(ctx.close(), loop).result(timeout)
    except Exception:
        pass
    return True


# ===========================================================================
# Browser
# ===========================================================================
async def _open(p, headless):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    if os.environ.get("CHIMERA_WA_FORCE_HEADLESS") == "1":   # automated tests only (no screen)
        headless = True
    kw = dict(user_data_dir=PROFILE_DIR, headless=headless, viewport={"width": 1280, "height": 860},
              args=["--disable-blink-features=AutomationControlled"])
    try:
        return await p.chromium.launch_persistent_context(channel="chrome", **kw)
    except Exception:
        return await p.chromium.launch_persistent_context(**kw)


async def _state(page, timeout_s, ready_only=False):
    """
    'ready' when the chat list shows, 'qr' when WhatsApp asks to link the
    device. ready_only=True keeps waiting through the QR screen (used while
    the user is scanning).
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if await page.locator(SEL_READY).count():
            return "ready"
        if not ready_only and await page.locator(SEL_QR).count():
            return "qr"
        await page.wait_for_timeout(500)
    return "timeout"


async def _shot(page, name):
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        path = os.path.join(SHOT_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_{name}.png")
        await page.screenshot(path=path)
        return path
    except Exception:
        return None


async def _first(page, selector, timeout_ms=10000):
    loc = page.locator(selector)
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        n = await loc.count()
        for i in range(n):
            if await loc.nth(i).is_visible():
                return loc.nth(i)
        await page.wait_for_timeout(250)
    return None


# ---------------------------------------------------------------------------
# Clicking robustly + knowing which step we're on (for clear error messages)
# ---------------------------------------------------------------------------
_STEP = {"name": ""}


def _at(step):
    _STEP["name"] = step


async def _click(loc, what, js_ok=True, escape=True):
    """
    Click like a person; if WhatsApp has something on top (a banner, a tip, a
    pop-up), press Escape and force the click; finally click from inside the page.
    Never waits Playwright's default 30 s.
    """
    try:
        await loc.click(timeout=6000)
        return
    except Exception:
        pass
    try:
        if escape:
            await loc.page.keyboard.press("Escape")
        await loc.click(timeout=4000, force=True)
        return
    except Exception:
        pass
    try:                                    # a real mouse click at its centre
        box = await loc.bounding_box()
        if box:
            await loc.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            return
    except Exception:
        pass
    if js_ok:
        try:
            await loc.evaluate("e => e.click()")
            return
        except Exception:
            pass
    raise WAError(f"Couldn't click {what} — something on WhatsApp's screen was in the way.")


async def _open_chat(page, name):
    _at(f"opening the chat '{name}'")
    await page.keyboard.press("Escape")
    box = await _first(page, SEL_SEARCH, 10000)
    if box is None:
        raise WAError("Couldn't find WhatsApp's search box.")
    await _click(box, "the search box")
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await box.type(name, delay=25)
    await page.wait_for_timeout(1200)
    hit = None
    deadline = time.time() + 10
    while time.time() < deadline and hit is None:
        cands = page.locator("#pane-side span[title], div[aria-label*='Search results' i] span[title]")
        for i in range(await cands.count()):
            t = (await cands.nth(i).get_attribute("title") or "").strip()
            if t.lower() == name.strip().lower() and await cands.nth(i).is_visible():
                hit = cands.nth(i)
                break
        if hit is None:
            await page.wait_for_timeout(400)
    if hit is None:
        raise WAError(f"No chat called '{name}' found. Check the exact name (it must match WhatsApp).")
    await _click(hit, f"the chat '{name}'")
    header = page.locator("#main header")
    try:
        await header.wait_for(state="visible", timeout=8000)
    except Exception:
        raise WAError(f"Clicked '{name}' but the chat didn't open.")
    if name.strip().lower() not in (await header.inner_text()).lower():
        raise WAError(f"The open chat isn't '{name}' — stopped to avoid sending to the wrong chat.")
    # clear the search so the next chat search starts clean
    try:
        await box.click(timeout=4000)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
    except Exception:
        pass


async def _wait_sent(page, timeout_s=90):
    """Waits until no outgoing message in the open chat still shows the pending clock."""
    await page.wait_for_timeout(1200)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if await page.locator(SEL_PENDING).count() == 0:
            return True
        await page.wait_for_timeout(700)
    return False


_PICK_PHOTO_INPUT_JS = r"""
([token]) => {
  // Only the "Photos & videos" input: it accepts video too and takes several
  // files. The image-only, single-file input is WhatsApp's STICKER maker —
  // never use it (it turns report pages into stickers).
  const ins = [...document.querySelectorAll("input[type='file']")]
    .filter(i => /image/.test(i.accept || '') && /video/.test(i.accept || ''));
  ins.sort((a, b) => (b.multiple ? 1 : 0) - (a.multiple ? 1 : 0));
  if (!ins.length) return null;
  ins[0].setAttribute('data-chimera-input', token);
  return {multiple: !!ins[0].multiple, accept: ins[0].accept || ''};
}
"""
SEL_SEND_MEDIA = ("div[role='button'][aria-label='Send'], button[aria-label='Send'], [aria-label='Send' i], "
                  "span[data-icon='send'], span[data-icon*='send'], div[role='button'] span[data-icon*='send']")


_MENU_TEXT = {"photos": re.compile(r"^\s*photos?\s*(&|and)\s*videos?\s*$", re.I),
              "document": re.compile(r"^\s*documents?\s*$", re.I)}
_ATTACH_NAME = re.compile(r"attach", re.I)

_PAGE_DUMP_JS = r"""
() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const d = e => [e.tagName.toLowerCase(), e.getAttribute('role') && 'role=' + e.getAttribute('role'),
                  e.getAttribute('aria-label') && 'aria=' + e.getAttribute('aria-label'),
                  e.getAttribute('title') && 'title=' + e.getAttribute('title'),
                  e.querySelector && e.querySelector('[data-icon]') && 'icon=' + e.querySelector('[data-icon]').getAttribute('data-icon'),
                  (e.innerText || '').trim().split('\n')[0].slice(0, 40)].filter(Boolean).join(' | ');
  return {
    footer: [...document.querySelectorAll('#main footer button, #main footer [role=button], #main footer span[data-icon]')].filter(vis).map(d).slice(0, 25),
    menu: [...document.querySelectorAll('li, [role=menuitem], [role=button], button')]
            .filter(e => vis(e) && !e.closest('#pane-side, #side')).map(d).slice(-30),
    inputs: [...document.querySelectorAll("input[type='file']")].map(i => `accept=${i.accept} multiple=${i.multiple}`),
    send_like: [...document.querySelectorAll("[aria-label*='send' i], [data-icon*='send'], [data-icon*='ptt']")].filter(vis)
                 .map(e => { const r = e.getBoundingClientRect(); return d(e) + ` | at ${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)}`
                             + (e.closest('#main footer') ? ' | in chat footer' : ''); }),
    text_boxes: [...document.querySelectorAll("[contenteditable='true']")].filter(vis)
                 .map(e => `${e.getAttribute('aria-label') || ''} / ${e.getAttribute('aria-placeholder') || ''}` + (e.closest('#main footer') ? ' | in chat footer' : '')),
  };
}
"""


_DROP_FILES_JS = r"""
async ([items]) => {
  // Drop the images on the open chat, like dragging them in from Explorer.
  const dt = new DataTransfer();
  for (const it of items) {
    const bin = atob(it.b64); const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    dt.items.add(new File([u8], it.name, {type: it.type}));
  }
  const target = document.querySelector('#main [role=application]') || document.querySelector('#main .copyable-area')
              || document.querySelector('#main') ;
  if (!target) return false;
  for (const type of ['dragenter', 'dragover', 'drop']) {
    target.dispatchEvent(new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: dt}));
    await new Promise(r => setTimeout(r, 150));
  }
  return true;
}
"""


async def _drop_files(page, files):
    """Drops the images on the chat. First a real (trusted) drag & drop through Chrome's DevTools,
    then a page-level drop event."""
    try:
        box = await page.locator("#main").first.bounding_box()
        if box:
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            cdp = await page.context.new_cdp_session(page)
            data = {"items": [], "files": [os.path.abspath(f) for f in files], "dragOperationsMask": 1}
            for t in ("dragEnter", "dragOver", "drop"):
                await cdp.send("Input.dispatchDragEvent", {"type": t, "x": x, "y": y, "data": data})
                await page.wait_for_timeout(150)
            send, box, _how = await _preview(page, 5000)
            if send is not None or box is not None:
                return True
    except Exception:
        pass
    import base64
    items = []
    for f in files:
        with open(f, "rb") as fh:
            items.append({"name": os.path.basename(f), "type": ("image/png" if f.lower().endswith(".png") else "application/pdf"
                                   if f.lower().endswith(".pdf") else "image/jpeg"),
                          "b64": base64.b64encode(fh.read()).decode()})
    return await page.evaluate(_DROP_FILES_JS, [items])


async def _dump_page(page, name):
    """Saves what WhatsApp Web showed (buttons, menu, upload fields) next to the screenshot, for diagnosis."""
    try:
        info = await page.evaluate(_PAGE_DUMP_JS)
        os.makedirs(SHOT_DIR, exist_ok=True)
        path = os.path.join(SHOT_DIR, f"{datetime.now():%Y%m%d_%H%M%S}_{name}.txt")
        with open(path, "w", encoding="utf-8") as f:
            for k, v in info.items():
                f.write(f"== {k}\n" + "\n".join(v) + "\n\n")
        return path
    except Exception:
        return None


async def _visible(loc):
    try:
        n = await loc.count()
        for i in range(n):
            if await loc.nth(i).is_visible():
                return loc.nth(i)
    except Exception:
        pass
    return None


async def _menu_item(page, which, timeout_ms=5000):
    """The attach-menu entry ('Photos & videos' or 'Document') as a person sees it."""
    pat = _MENU_TEXT[which]
    deadline = time.time() + timeout_ms / 1000
    while True:
        for loc in (page.get_by_role("button", name=pat), page.get_by_role("menuitem", name=pat),
                    page.get_by_role("listitem").filter(has_text=pat), page.get_by_text(pat)):
            el = await _visible(loc)
            if el is not None:
                return el
        if time.time() >= deadline:
            return None
        await page.wait_for_timeout(300)


async def _open_attach(page):
    """Opens the + (Attach) menu in the chat. Returns True when its entries are showing."""
    for attempt in range(2):
        btn = await _visible(page.locator("#main footer").get_by_role("button", name=_ATTACH_NAME))
        if btn is None:
            btn = await _first(page, SEL_ATTACH, 8000)
        if btn is None:
            raise WAError("Couldn't find the attach (+) button.")
        await _click(btn, "the attach (+) button")
        if await _menu_item(page, "document", 2500) or await _menu_item(page, "photos", 500):
            return True
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    return False


async def _attach(page, files, which):
    """
    Gets files into WhatsApp's send preview: photos through 'Photos & videos',
    PDFs through 'Document' — exactly like a person. Returns how many it took.
    Fallbacks: the matching hidden upload field, then dropping the files on the chat.
    Never the sticker upload.
    """
    label = "Photos & videos" if which == "photos" else "Document"
    _at(f"attaching through '{label}'")
    await _cancel_recording(page)
    menu_open = await _open_attach(page)
    item = await _menu_item(page, which, 3000) if menu_open else None
    if item is not None:
        try:
            async with page.expect_file_chooser(timeout=8000) as fc:
                await _click(item, f"'{label}'", js_ok=False, escape=False)
            chooser = await fc.value
            take = files if chooser.is_multiple() else files[:1]
            await chooser.set_files(take)
            return len(take)
        except Exception as e:
            WA.log(f"Clicked '{label}' but no file window opened ({str(e).splitlines()[0][:80]}) — trying another way.",
                   "warn")
    token = uuid.uuid4().hex[:8]
    if which == "photos":
        info = await page.evaluate(_PICK_PHOTO_INPUT_JS, [token])
        if info:
            take = files if info["multiple"] else files[:1]
            await page.locator(f"input[data-chimera-input='{token}']").set_input_files(take)
            return len(take)
    dump = await _dump_page(page, f"attach_{which}")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    WA.log(f"'{label}' didn't work here — dropping the files on the chat instead."
           + (f" (Page details saved: {dump})" if dump else ""), "warn")
    if await _drop_files(page, files):
        return len(files)
    raise WAError(f"Couldn't attach the files through '{label}' or by dropping them on the chat."
                  + (f" Page details: {dump}" if dump else ""))


_PREVIEW_JS = r"""
([token]) => {
  // WhatsApp's send preview (after attaching): its own message box ("Add a caption"
  // / "Type a message") and its green Send button. NEVER anything in the normal
  // chat bar (#main footer): that's where the green MICROPHONE is.
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  const W = innerWidth, H = innerHeight;
  const outside = e => !e.closest('#pane-side, #side, header, #main footer');
  const words = e => ((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('data-icon') || '') + ' ' +
                      [...e.querySelectorAll('[data-icon], [aria-label]')].map(x => (x.getAttribute('data-icon') || '') + ' ' +
                      (x.getAttribute('aria-label') || '')).join(' '));
  const notMic = e => !/voice|ptt|mic|audio|record|sticker|gif|emoji/i.test(words(e));
  const desc = e => `${e.tagName.toLowerCase()}${e.getAttribute('aria-label') ? ' "' + e.getAttribute('aria-label') + '"' : ''}` +
                    `${e.querySelector('[data-icon]') ? ' icon=' + e.querySelector('[data-icon]').getAttribute('data-icon') : ''}`;
  // the preview's message box (outside the chat bar)
  const boxes = [...document.querySelectorAll("[contenteditable='true']")].filter(e => vis(e) && outside(e));
  const named = boxes.filter(e => /caption|message/i.test((e.getAttribute('aria-label') || '') + ' ' +
                                   (e.getAttribute('aria-placeholder') || '') + ' ' + (e.getAttribute('data-placeholder') || '')));
  const box = (named.length ? named : boxes).slice(-1)[0] || null;
  let send = null, how = '';
  const byLabel = [...document.querySelectorAll('[aria-label]')]
    .filter(e => vis(e) && outside(e) && notMic(e) && /^send(\s*\(?\d*\)?)?$/i.test((e.getAttribute('aria-label') || '').trim()));
  if (byLabel.length) { send = byLabel[byLabel.length - 1]; how = 'label ' + desc(send); }
  if (!send) {
    const icons = [...document.querySelectorAll("[data-icon*='send']")]
      .filter(e => vis(e) && outside(e) && !/voice|ptt|mic|record/i.test(e.getAttribute('data-icon') || ''));
    if (icons.length) { const i = icons[icons.length - 1]; send = i.closest("[role=button], button") || i;
                        if (!notMic(send)) send = null; else how = 'icon ' + i.getAttribute('data-icon'); }
  }
  if (!send && box) {   // only when the preview is surely open: its big green round button, lower right
    const greens = [...document.querySelectorAll('div, span, button')].filter(e => {
      if (!vis(e) || !outside(e) || !notMic(e)) return false;
      const r = e.getBoundingClientRect();
      if (r.width < 36 || r.width > 90 || Math.abs(r.width - r.height) > 8 || r.left < W * 0.45 || r.top < H * 0.55) return false;
      const m = getComputedStyle(e).backgroundColor.match(/\d+/g); if (!m) return false;
      const [R, G, B] = m.map(Number);
      return G > 140 && R < 110 && B < 160 && G - R > 60;
    });
    if (greens.length) { greens.sort((a, b) => b.getBoundingClientRect().right + b.getBoundingClientRect().bottom
                                            - a.getBoundingClientRect().right - a.getBoundingClientRect().bottom);
                         send = greens[0]; how = 'green round button ' + desc(send); }
  }
  // Return where they are on screen: WhatsApp redraws these elements often, so
  // Chimera clicks the spot (like a person) instead of holding on to the element.
  const pt = e => { if (!e) return null; e.scrollIntoView({block: 'nearest'}); const r = e.getBoundingClientRect();
                    return {x: r.left + r.width / 2, y: r.top + r.height / 2}; };
  if (token === 'click-send' && send) send.click();   // in-page click (when something covers the spot)
  return {send: pt(send), how, box: pt(box), open: !!(box || send)};
}
"""
_RECORDING_JS = r"""
() => {
  // A voice recording in the chat bar (timer + delete): cancel it, never send it.
  const f = document.querySelector('#main footer'); if (!f) return false;
  const txt = f.innerText || '';
  const rec = /\b\d{1,2}:\d{2}\b/.test(txt) && f.querySelector("[aria-label*='delete' i], [aria-label*='cancel' i], [data-icon*='delete'], [data-icon*='trash']");
  if (!rec) return false;
  const del = f.querySelector("[aria-label*='delete' i], [aria-label*='cancel' i]") ||
              (f.querySelector("[data-icon*='delete'], [data-icon*='trash']") || {}).closest?.('[role=button], button');
  if (del) { del.click(); return true; }
  return false;
}
"""


async def _cancel_recording(page):
    try:
        if await page.evaluate(_RECORDING_JS):
            WA.log("A voice recording had started in the chat — cancelled it (nothing was sent).", "warn")
            await page.wait_for_timeout(500)
    except Exception:
        pass


async def _preview(page, timeout_ms=20000):
    """Waits for the send preview. Returns (Send button point | None, message box point | None, how)."""
    deadline = time.time() + timeout_ms / 1000
    while True:
        r = await page.evaluate(_PREVIEW_JS, [""])
        if r["open"] or time.time() >= deadline:
            return r["send"], r["box"], r["how"]
        await page.wait_for_timeout(400)


async def _tap(page, pt):
    await page.mouse.click(pt["x"], pt["y"])


async def _send_files(page, files, caption, which="photos"):
    """Attach (photos or PDF documents) and send, in as many messages as WhatsApp needs."""
    i, first = 0, True
    what = "images" if which == "photos" else "PDF(s)"
    while i < len(files):
        n = await _attach(page, files[i:], which)
        if n < len(files) - i and first:
            WA.log(f"WhatsApp took one file at a time here — sending {len(files)} {what} one by one.")
        send, box, how = await _preview(page)
        if send is None and box is None:
            shot = await _shot(page, "no_preview")
            dump = await _dump_page(page, "no_preview")
            raise WAError("WhatsApp didn't open the send preview." + (f" Screenshot: {shot}" if shot else "")
                          + (f" Page details: {dump}" if dump else ""))
        _at("sending from WhatsApp's preview")
        await page.wait_for_timeout(600)               # let the preview settle
        if caption and first:
            send, box, how = await _preview(page, 3000)
            if box is not None:
                await _tap(page, box)
                await page.keyboard.insert_text(caption)   # in one go (WhatsApp redraws while typing)
                await page.wait_for_timeout(300)
        send, box, how = await _preview(page, 3000)   # positions fresh, right before sending
        if send is not None:
            WA.log(f"Pressing Send ({how}).")
            await _tap(page, send)                     # the green Send button
        elif box is not None:
            await _tap(page, box)
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(1500)
        still_send, box2, _h = await _preview(page, 1500)
        if still_send is not None or box2 is not None:     # preview still open: try the other way once
            await page.evaluate(_PREVIEW_JS, ["click-send"])
            await page.wait_for_timeout(1500)
            still_send, box2, _h = await _preview(page, 1500)
            if box2 is not None:
                await _tap(page, box2)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1500)
            still_send, box2, _h = await _preview(page, 1500)
            if still_send is not None or box2 is not None:
                await _cancel_recording(page)
                shot = await _shot(page, "send_button")
                dump = await _dump_page(page, "send_button")
                raise WAError(f"The preview opened but pressing Send didn't send it (found Send by {how})."
                              + (f" Screenshot: {shot}" if shot else "") + (f" Page details: {dump}" if dump else ""))
        await _cancel_recording(page)
        if not await _wait_sent(page):
            raise WAError("WhatsApp is still sending after 90 s (slow connection?). Check the chat.")
        i += n
        first = False


async def _send_images(page, files, caption):
    await _send_files(page, files, caption, "photos")


async def _send_text(page, text):
    box = await _first(page, SEL_COMPOSE, 8000)
    if box is None:
        raise WAError("Couldn't find the message box.")
    await _click(box, "the message box")
    await box.type(text, delay=15)
    await page.keyboard.press("Enter")
    if not await _wait_sent(page, 45):
        raise WAError("The test message is still pending.")


async def _session(action, headless, fn, link_timeout=180):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        ctx = await _open(p, headless)
        _ACTIVE.update(loop=asyncio.get_running_loop(), ctx=ctx)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            WA.log("Opening WhatsApp Web…")
            await page.goto(WA_URL, wait_until="domcontentloaded", timeout=60000)
            st = await _state(page, 60)
            if st == "qr":
                if action != "link":
                    raise WAError("WhatsApp isn't linked yet (or was logged out). Use 'Link WhatsApp' and scan the QR code.")
                WA.log("Scan the QR code with WhatsApp on your phone: Settings → Linked devices → Link a device.")
                st = await _state(page, link_timeout, ready_only=True)
            if st != "ready":
                shot = await _shot(page, "not_ready")
                raise WAError("WhatsApp Web didn't finish loading." + (f" Screenshot: {shot}" if shot else ""))
            await page.wait_for_timeout(1500)
            return await fn(page)
        finally:
            _ACTIVE.update(loop=None, ctx=None)
            try:
                await ctx.close()
            except Exception:
                pass


def _run(action, headless, fn):
    try:
        res = asyncio.run(_session(action, headless, fn))
        WA.finish(res)
        return res
    except WAError as e:
        WA.log(str(e), "error")
        res = {"ok": False, "message": str(e)}
        WA.finish(res)
        return res
    except Exception as e:
        msg = (f"Stopped while {_STEP['name']}: " if _STEP["name"] else "") + str(e).splitlines()[0][:300]
        WA.log(msg, "error")
        res = {"ok": False, "message": f"WhatsApp error: {msg}"}
        WA.finish(res)
        return res


# ===========================================================================
# Public actions (each runs in a background thread; UI polls WA.snapshot())
# ===========================================================================
def _start(action, target):
    if not WA.start(action):
        return {"ok": False, "message": "WhatsApp is busy with another task."}
    threading.Thread(target=target, daemon=True, name=f"wa-{action}").start()
    return {"ok": True, "message": "Started."}


def link_whatsapp():
    async def fn(page):
        AS.save_automation_settings({"wa_chats_cache": json.dumps(await _read_chats(page))})
        WA.log("WhatsApp is linked. Chimera will stay signed in.")
        return {"ok": True, "message": "WhatsApp linked."}
    return _start("link", lambda: _run("link", False, fn))


_CHAT_NAMES_JS = r"""
() => {
  // Only the contact / group NAME of each chat row (the first title in the row) —
  // not the last-message preview, which also carries a title.
  const pane = document.querySelector('#pane-side'); if (!pane) return [];
  const rows = [...pane.querySelectorAll("[role=listitem], [role=row]")];
  const names = rows.length
    ? rows.map(r => { const t = r.querySelector('span[title]'); return t ? t.getAttribute('title') : ''; })
    : [...pane.querySelectorAll('span[title]')].filter(s => {
        const row = s.closest('div[tabindex], [aria-selected]') || s.parentElement;
        return row.querySelector('span[title]') === s;          // first title in its row
      }).map(s => s.getAttribute('title'));
  return names.map(n => (n || '').trim()).filter(Boolean);
}
"""


async def _read_chats(page, scrolls=12):
    names = []
    pane = page.locator("#pane-side")
    for _ in range(scrolls):
        found = await page.evaluate(_CHAT_NAMES_JS)
        for n in found:
            if n not in names and not re.match(r"^\d{1,2}:\d{2}", n):
                names.append(n)
        if await pane.count():
            await pane.evaluate("el => el.scrollBy(0, el.clientHeight)")
        await page.wait_for_timeout(400)
    return names[:400]


def load_chats():
    def go():
        s = AS.get_automation_settings()

        async def fn(page):
            names = await _read_chats(page)
            AS.save_automation_settings({"wa_chats_cache": json.dumps(names)})
            WA.log(f"Found {len(names)} chats.")
            return {"ok": True, "message": f"Found {len(names)} chats.", "chats": names}
        _run("chats", s["wa_show_browser"] != "1", fn)
    return _start("chats", go)


def send_test(chat_name):
    def go():
        s = AS.get_automation_settings()

        async def fn(page):
            await _open_chat(page, chat_name)
            await _send_text(page, f"Chimera test message ({datetime.now():%Y-%m-%d %H:%M}) — WhatsApp delivery works.")
            _log_delivery(None, chat_name, 0, "sent", "Test message")
            WA.log(f"Test message sent to {chat_name}.")
            return {"ok": True, "message": f"Test message sent to {chat_name}."}
        _run("test", s["wa_show_browser"] != "1", fn)
    return _start("test", go)


def deliver_run(report_run_id, rule_ids=None, wait=False):
    """Sends a saved report run to every enabled rule (or just rule_ids)."""
    run = report_pipeline.get_run(report_run_id)
    if not run:
        return {"ok": False, "message": "That report run no longer exists."}
    if run["status"] == "pending_review":
        return {"ok": False, "message": "Save (approve) the preview before sending it."}
    rules = [r for r in list_rules() if r["enabled"] and (not rule_ids or r["id"] in rule_ids)]
    if not rules:
        return {"ok": False, "message": "No WhatsApp delivery rules are set up."}
    plan = [p for p in plan_delivery(run, rules)]
    styles_built = sorted({o["style"] for rep in run["summary"].get("reports", []) for o in rep.get("outputs", [])})
    _wa = AS.get_automation_settings()["wa_style"]
    used = _wa if _wa in styles_built else (styles_built[0] if styles_built else "?")
    if not any(p["files"] or p.get("pdfs") for p in plan):
        built = sum(len(r.get("images") or []) for r in run["summary"].get("reports", []))
        return {"ok": False, "message": ("No WhatsApp images were made for this run — install the missing package: "
                                         "py -m pip install -r requirements.txt, then build the reports again.")
                if not built else
                "None of the built pages match your delivery rules (check each rule's reports and manager groups)."}

    def go():
        s = AS.get_automation_settings()

        async def fn(page):
            sent, failed = 0, 0
            WA.log(f"Sending the {used} reports (built: {', '.join(styles_built) or 'none'}"
                   + ("; choose which in WhatsApp › Sending options" if len(styles_built) > 1 else "") + ").")
            for item in plan:
                pdfs = item.get("pdfs") or []
                if not item["files"] and not pdfs:
                    _log_delivery(report_run_id, item["chat_name"], 0, "skipped", "No matching report pages.")
                    WA.log(f"{item['chat_name']}: nothing to send for this rule.", "warn")
                    continue
                missing = [f for f in item["files"] + pdfs if not os.path.exists(f)]
                if missing:
                    _log_delivery(report_run_id, item["chat_name"], 0, "failed", "Report files are missing on disk.")
                    failed += 1
                    continue
                try:
                    WA.log(f"Opening '{item['chat_name']}'…")
                    await _open_chat(page, item["chat_name"])
                    for bt in item.get("batches") or []:          # SKU, then Pivot, then Hourly
                        for k in range(0, len(bt["files"]), BATCH):
                            chunk = bt["files"][k:k + BATCH]
                            await _send_files(page, chunk, bt["caption"] if k == 0 else "", "photos")
                            WA.log(f"{item['chat_name']}: sent {len(chunk)} {bt['label']} image(s) "
                                   f"({item.get('orientation', 'portrait')}).")
                    if pdfs:
                        await _send_files(page, pdfs, "" if item["files"] else item["caption"], "document")
                        WA.log(f"{item['chat_name']}: sent {len(pdfs)} PDF(s) (Document).")
                    _log_delivery(report_run_id, item["chat_name"], len(item["files"]) + len(pdfs), "sent", item["caption"])
                    sent += 1
                except Exception as e:
                    shot = await _shot(page, "send_failed")
                    msg = str(e) if isinstance(e, WAError) else \
                        f"Stopped while {_STEP['name'] or 'working in WhatsApp'}: {str(e).splitlines()[0][:160]}"
                    _log_delivery(report_run_id, item["chat_name"], 0, "failed", msg + (f" ({shot})" if shot else ""))
                    WA.log(f"{item['chat_name']}: {msg}", "error")
                    failed += 1
                    try:                                  # leave a clean screen for the next chat
                        await page.keyboard.press("Escape")
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
            msg = f"Sent to {sent} chat(s)" + (f", {failed} failed" if failed else "") + "."
            return {"ok": failed == 0 and sent > 0, "message": msg, "sent": sent, "failed": failed}
        return _run("send", s["wa_show_browser"] != "1", fn)

    if wait:
        if not WA.start("send"):
            return {"ok": False, "message": "WhatsApp is busy with another task."}
        return go()
    return _start("send", go)


def linked_status():
    exists = os.path.isdir(PROFILE_DIR) and any(os.scandir(PROFILE_DIR))
    return {"profile_exists": exists, "chats": json.loads(AS.get_automation_settings()["wa_chats_cache"] or "[]")}
