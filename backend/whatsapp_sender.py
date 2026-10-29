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
    conn.commit()
    conn.close()


def list_rules():
    conn = db.get_connection()
    rows = conn.execute("SELECT id, chat_name, kinds, groups, caption, enabled FROM wa_rules ORDER BY id").fetchall()
    conn.close()
    return [{"id": r[0], "chat_name": r[1], "kinds": [k for k in r[2].split(",") if k],
             "groups": json.loads(r[3] or '["*"]'), "caption": r[4], "enabled": bool(r[5])} for r in rows]


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
    conn = db.get_connection()
    try:
        vals = (chat, ",".join(kinds), json.dumps(groups), caption, 1 if data.get("enabled", True) else 0)
        if data.get("id"):
            n = conn.execute("UPDATE wa_rules SET chat_name=?, kinds=?, groups=?, caption=?, enabled=? WHERE id=?",
                             vals + (data["id"],)).rowcount
            if not n:
                return False, "That rule no longer exists."
        else:
            conn.execute("INSERT INTO wa_rules (chat_name, kinds, groups, caption, enabled) VALUES (?, ?, ?, ?, ?)",
                         vals)
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


def plan_delivery(run, rules=None):
    """Which images go to which chat for this report run. Pure — no browser."""
    s = AS.get_automation_settings()
    styles_built = {o["style"] for rep in run["summary"].get("reports", []) for o in rep.get("outputs", [])}
    style = s["wa_style"] if s["wa_style"] in styles_built else (sorted(styles_built)[0] if styles_built else None)
    items = report_pipeline.image_items(run, style)
    plan = []
    for rule in (rules if rules is not None else [r for r in list_rules() if r["enabled"]]):
        chosen = [it for it in items if it["kind"] in rule["kinds"]
                  and ("*" in rule["groups"] or it["group"] == "*" or it["group"] in rule["groups"])]
        caption = (rule["caption"] or "").replace("{date}", run["report_date"]) \
            .replace("{report}", ", ".join(sorted({it["label"] for it in chosen}))) \
            .replace("{time}", datetime.now().strftime("%H:%M"))
        plan.append({"rule_id": rule["id"], "chat_name": rule["chat_name"], "files": [it["path"] for it in chosen],
                     "pages": [it["page"] for it in chosen], "caption": caption})
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


async def _open_chat(page, name):
    box = await _first(page, SEL_SEARCH, 10000)
    if box is None:
        raise WAError("Couldn't find WhatsApp's search box.")
    await box.click()
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
    await hit.click()
    header = page.locator("#main header")
    try:
        await header.wait_for(state="visible", timeout=8000)
    except Exception:
        raise WAError(f"Clicked '{name}' but the chat didn't open.")
    if name.strip().lower() not in (await header.inner_text()).lower():
        raise WAError(f"The open chat isn't '{name}' — stopped to avoid sending to the wrong chat.")
    # clear the search so the next chat search starts clean
    try:
        await box.click()
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
  // WhatsApp Web has several file inputs after "Attach": a single-file one for
  // stickers and a multi-file "Photos & videos" one. Prefer the multi-file
  // input that also accepts video (= Photos & videos), never the sticker one.
  const ins = [...document.querySelectorAll("input[type='file']")]
    .filter(i => /image/.test(i.accept || ''));
  const score = i => (i.multiple ? 4 : 0) + (/video/.test(i.accept || '') ? 2 : 0) + (/webp/.test(i.accept || '') ? -3 : 0);
  ins.sort((a, b) => score(b) - score(a));
  if (!ins.length) return null;
  ins[0].setAttribute('data-chimera-input', token);
  return {multiple: !!ins[0].multiple, accept: ins[0].accept || '', count: ins.length};
}
"""


async def _open_attach(page):
    attach = await _first(page, SEL_ATTACH, 8000)
    if attach is None:
        raise WAError("Couldn't find the attach (+) button.")
    await attach.click()
    await page.wait_for_timeout(700)


async def _send_images(page, files, caption):
    """Attach the images (several at once when WhatsApp allows it, else one per message) and send."""
    await _open_attach(page)
    token = uuid.uuid4().hex[:8]
    info = await page.evaluate(_PICK_PHOTO_INPUT_JS, [token])
    if not info:
        raise WAError("Couldn't find WhatsApp's photo upload field.")
    inp = page.locator(f"input[data-chimera-input='{token}']")
    if not info["multiple"] and len(files) > 1:
        # only a single-file field: send the pages one by one (caption on the first)
        WA.log(f"WhatsApp accepts one image at a time here — sending {len(files)} images one by one.")
        await page.keyboard.press("Escape")
        for n, f in enumerate(files):
            await _send_images(page, [f], caption if n == 0 else "")
        return
    await inp.set_input_files(files)
    cap = await _first(page, SEL_CAPTION, 15000)
    if cap is not None and caption:
        await cap.click()
        await cap.type(caption, delay=10)
    send = await _first(page, SEL_SEND, 15000)
    if send is None:
        raise WAError("The image preview opened but the Send button wasn't found.")
    await send.click()
    if not await _wait_sent(page):
        raise WAError("WhatsApp is still sending after 90 s (slow connection?). Check the chat.")


async def _send_text(page, text):
    box = await _first(page, SEL_COMPOSE, 8000)
    if box is None:
        raise WAError("Couldn't find the message box.")
    await box.click()
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
        msg = str(e).splitlines()[0][:300]
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


async def _read_chats(page, scrolls=12):
    names = []
    pane = page.locator("#pane-side")
    for _ in range(scrolls):
        found = await page.evaluate("""() => [...document.querySelectorAll('#pane-side span[title]')]
                                       .map(s => s.getAttribute('title').trim()).filter(Boolean)""")
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
    if not any(p["files"] for p in plan):
        built = sum(len(r.get("images") or []) for r in run["summary"].get("reports", []))
        return {"ok": False, "message": ("No WhatsApp images were made for this run — install the missing package: "
                                         "py -m pip install -r requirements.txt, then build the reports again.")
                if not built else
                "None of the built pages match your delivery rules (check each rule's reports and manager groups)."}

    def go():
        s = AS.get_automation_settings()

        async def fn(page):
            sent, failed = 0, 0
            WA.log(f"Sending the {used} images (built: {', '.join(styles_built) or 'none'}"
                   + ("; choose which in WhatsApp › Sending options" if len(styles_built) > 1 else "") + ").")
            for item in plan:
                if not item["files"]:
                    _log_delivery(report_run_id, item["chat_name"], 0, "skipped", "No matching report pages.")
                    WA.log(f"{item['chat_name']}: nothing to send for this rule.", "warn")
                    continue
                missing = [f for f in item["files"] if not os.path.exists(f)]
                if missing:
                    _log_delivery(report_run_id, item["chat_name"], 0, "failed", "Report images are missing on disk.")
                    failed += 1
                    continue
                try:
                    WA.log(f"Opening '{item['chat_name']}'…")
                    await _open_chat(page, item["chat_name"])
                    for b in range(0, len(item["files"]), BATCH):
                        chunk = item["files"][b:b + BATCH]
                        await _send_images(page, chunk, item["caption"] if b == 0 else "")
                        WA.log(f"{item['chat_name']}: sent {len(chunk)} image(s).")
                    _log_delivery(report_run_id, item["chat_name"], len(item["files"]), "sent", item["caption"])
                    sent += 1
                except WAError as e:
                    shot = await _shot(page, "send_failed")
                    _log_delivery(report_run_id, item["chat_name"], 0, "failed", str(e) + (f" ({shot})" if shot else ""))
                    WA.log(f"{item['chat_name']}: {e}", "error")
                    failed += 1
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
