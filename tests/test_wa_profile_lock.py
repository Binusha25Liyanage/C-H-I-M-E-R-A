"""
WhatsApp browser lock: only one Chimera task / process uses Chimera's WhatsApp
profile at a time; the others wait, and a lock left by a closed Chimera is ignored.

Run:  py -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

import whatsapp_sender as W  # noqa: E402


class ProfileLockTest(unittest.TestCase):
    def setUp(self):
        self.keep = W.PROFILE_LOCK
        W.PROFILE_LOCK = os.path.join(tempfile.mkdtemp(), "whatsapp.lock")

    def tearDown(self):
        W.PROFILE_LOCK = self.keep

    def test_takes_and_releases(self):
        self.assertTrue(W._lock_profile(wait_s=1))
        self.assertEqual(json.load(open(W.PROFILE_LOCK))["pid"], os.getpid())
        W._unlock_profile()
        self.assertFalse(os.path.exists(W.PROFILE_LOCK))

    def test_lock_of_a_closed_chimera_is_ignored(self):
        json.dump({"pid": 999999, "since": time.time()}, open(W.PROFILE_LOCK, "w"))
        self.assertTrue(W._lock_profile(wait_s=1))
        W._unlock_profile()

    def test_waits_for_another_process_then_continues(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            json.dump({"pid": other.pid, "since": time.time(), "action": "send"}, open(W.PROFILE_LOCK, "w"))
            with self.assertRaises(W.WAError):            # still busy after the wait
                W._lock_profile(wait_s=1)
            threading.Timer(1.5, lambda: os.remove(W.PROFILE_LOCK)).start()   # the other one finishes
            t0 = time.time()
            self.assertTrue(W._lock_profile(wait_s=10))
            self.assertGreater(time.time() - t0, 1.0)
            W._unlock_profile()
        finally:
            other.kill()

    def test_never_removes_someone_elses_lock(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            json.dump({"pid": other.pid, "since": time.time()}, open(W.PROFILE_LOCK, "w"))
            W._unlock_profile()
            self.assertTrue(os.path.exists(W.PROFILE_LOCK))
        finally:
            other.kill()

    def test_in_use_error_recognised(self):
        self.assertTrue(W._profile_in_use_error(Exception("BrowserType.launch_persistent_context: Opening in existing "
                                                          "browser session. This usually means that the profile is already in use")))
        self.assertFalse(W._profile_in_use_error(Exception("net::ERR_INTERNET_DISCONNECTED")))


if __name__ == "__main__":
    unittest.main()


class _FakePage:
    def __init__(self, closes):
        self.closes = closes

    async def goto(self, *a, **k):
        if self.closes:
            raise RuntimeError("Locator.count: Target page, context or browser has been closed")

    async def wait_for_timeout(self, ms):
        pass


class _FakeCtx:
    def __init__(self, closes):
        self.pages = [_FakePage(closes)]
        self.closed = False

    async def close(self):
        self.closed = True


class _FakePW:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class WindowClosedWhileLoadingTest(unittest.TestCase):
    """The WhatsApp window closing by itself while it loads (nothing sent yet) is retried once."""

    def _run(self, closes):
        import asyncio
        from unittest import mock
        ctxs = [_FakeCtx(c) for c in closes]
        opened = []

        async def fake_open(p, headless):
            opened.append(1)
            return ctxs[len(opened) - 1]

        async def ready(page, t, ready_only=False):
            return "ready"

        async def nothing(page, *a, **k):
            return None

        async def fn(page):
            return {"ok": True}

        fake_mod = mock.MagicMock(async_playwright=lambda: _FakePW())
        with mock.patch.dict(sys.modules, {"playwright.async_api": fake_mod}), \
                mock.patch.object(W, "_open", fake_open), mock.patch.object(W, "_state", ready), \
                mock.patch.object(W, "_let_queue_finish", nothing):
            try:
                return asyncio.run(W._session_locked("send", True, fn, 10)), len(opened)
            except Exception as e:
                return e, len(opened)

    def test_reopens_once(self):
        res, n = self._run([True, False])
        self.assertEqual(res, {"ok": True})
        self.assertEqual(n, 2)

    def test_gives_up_after_second_close(self):
        res, n = self._run([True, True])
        self.assertIsInstance(res, W.WAError)
        self.assertIn("closed by itself", str(res))
        self.assertEqual(n, 2)


class WhatsAppReloadsWhileSendingTest(unittest.TestCase):
    """WhatsApp Web reloading itself mid-send: before Send = clearly 'not sent'; after Send = wait, never send twice."""

    def _run(self, pressed_before_reload):
        import asyncio
        from unittest import mock
        calls = {"batch": 0, "waited": 0}

        async def batch(page, files, i, caption, which, first, what, st):
            calls["batch"] += 1
            st["n"] = len(files)
            st["pressed"] = pressed_before_reload
            raise RuntimeError("Page.evaluate: Execution context was destroyed, most likely because of a navigation")

        async def back(page, why=""):
            return None

        async def waited(page, t=150):
            calls["waited"] += 1
            return True

        with mock.patch.object(W, "_send_batch", batch), mock.patch.object(W, "_after_reload", back), \
                mock.patch.object(W, "_wait_sent", waited):
            try:
                asyncio.run(W._send_files(object(), ["a.png", "b.png"], "cap"))
                return None, calls
            except Exception as e:
                return e, calls

    def test_reload_before_send_says_not_sent(self):
        err, calls = self._run(False)
        self.assertIsInstance(err, W.WAError)
        self.assertIn("were not sent", str(err))
        self.assertEqual(calls["batch"], 1)

    def test_reload_after_send_waits_and_does_not_resend(self):
        err, calls = self._run(True)
        self.assertIsNone(err)
        self.assertEqual(calls["batch"], 1)          # not sent a second time
        self.assertEqual(calls["waited"], 1)


class _Loc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    async def count(self):
        if self.sel == "#pane-side":
            return 1
        if "span[title]" in self.sel:
            return len(self.page.titles())
        if self.sel == "#main header":
            return 1
        return 0

    def nth(self, i):
        return _Item(self.page, i)

    async def wait_for(self, **k):
        return None

    async def inner_text(self):
        return self.page.opened or ""


class _Item:
    def __init__(self, page, i):
        self.page, self.i = page, i

    async def get_attribute(self, a):
        return self.page.titles()[self.i]

    async def is_visible(self):
        return True

    async def click(self, **k):
        self.page.opened = self.page.titles()[self.i]


class _KB:
    def __init__(self, page):
        self.page = page

    async def press(self, k):
        if k == "Backspace":
            self.page.box = ""

    async def insert_text(self, t):
        if self.page.drop_typing:
            self.page.drop_typing -= 1          # WhatsApp redrew the box: the text went nowhere
            return
        self.page.box += t


class _Page:
    def __init__(self, recent, all_chats, drop_typing=0):
        self.recent, self.all, self.box, self.opened, self.drop_typing = recent, all_chats, "", None, drop_typing
        self.keyboard = _KB(self)

    def titles(self):
        return [c for c in self.all if self.box and self.box.lower() in c.lower()] if self.box else self.recent

    def locator(self, sel):
        return _Loc(self, sel)

    async def evaluate(self, js, *a):
        return self.box

    async def wait_for_timeout(self, ms):
        return None


class OpenChatTest(unittest.TestCase):
    """Opening a chat: recent chats are clicked straight from the list; otherwise the name goes into the search
    box in one go and Chimera checks it landed (WhatsApp redrawing the box made type() time out)."""

    def _open(self, page, name):
        import asyncio
        from unittest import mock

        async def first(p, sel, t=10000):
            return _Item(p, 0) if sel == W.SEL_SEARCH else None

        async def click(el, what):
            await el.click()

        async def nothing(*a, **k):
            return None
        with mock.patch.object(W, "_first", first), mock.patch.object(W, "_click", click):
            item_click = _Item.click

            async def box_or_chat_click(self, **k):     # clicking the search box doesn't open a chat
                if self.page.box == "" and self.page.titles() is self.page.recent and k.get("timeout"):
                    return None
                await item_click(self)
            with mock.patch.object(_Item, "click", box_or_chat_click):
                asyncio.run(W._open_chat(page, name))
        return page.opened

    def test_recent_chat_is_clicked_without_searching(self):
        p = _Page(["Daily Review", "Madam Lakmee"], ["Daily Review", "Madam Lakmee", "Old Group"])
        self.assertEqual(self._open(p, "Daily Review"), "Daily Review")
        self.assertEqual(p.box, "")

    def test_older_chat_found_by_search(self):
        p = _Page(["Daily Review"], ["Daily Review", "Old Group"])
        self.assertEqual(self._open(p, "Old Group"), "Old Group")

    def test_search_retries_when_the_text_went_nowhere(self):
        p = _Page(["Daily Review"], ["Daily Review", "Old Group"], drop_typing=1)
        self.assertEqual(self._open(p, "Old Group"), "Old Group")
