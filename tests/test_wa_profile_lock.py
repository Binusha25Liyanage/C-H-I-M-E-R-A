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
