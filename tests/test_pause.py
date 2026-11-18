"""
Pause / Resume of running processes: a paused run waits at its next safe point, carries on after Resume,
can still be stopped while paused, and stops by itself after the pause limit.

Run:  py -m unittest discover -s tests -v
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.environ.setdefault("HOME", tempfile.mkdtemp())

import sfa_automation as SA  # noqa: E402
import whatsapp_sender as W  # noqa: E402


def later(seconds, fn):
    t = threading.Timer(seconds, fn)
    t.start()
    return t


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.live = SA.LiveState()
        self.live.reset(kind="test")

    def test_not_paused_runs_straight_through(self):
        self.assertFalse(asyncio.run(self.live.checkpoint()))

    def test_pause_waits_until_resume(self):
        self.assertTrue(self.live.pause())
        self.assertFalse(self.live.pause())                       # already pausing
        self.assertEqual(self.live.snapshot()["pause"], "pausing")
        seen = {}
        later(0.3, lambda: seen.update(state=self.live.snapshot()["pause"]))
        later(0.8, self.live.resume)
        t0 = time.time()
        stopped = asyncio.run(self.live.checkpoint())
        self.assertFalse(stopped)
        self.assertGreaterEqual(time.time() - t0, 0.7)
        self.assertEqual(seen["state"], "paused")
        self.assertEqual(self.live.snapshot()["pause"], "")
        msgs = [x["msg"] for x in self.live.snapshot()["log"]]
        self.assertTrue(any(m.startswith("Paused.") for m in msgs) and "Resumed." in msgs)

    def test_stop_while_paused(self):
        self.live.pause()
        later(0.3, self.live.cancel)
        self.assertTrue(asyncio.run(self.live.checkpoint()))

    def test_several_lanes_wait_and_resume_together(self):
        self.live.pause()
        later(0.5, self.live.resume)

        async def lanes():
            return await asyncio.gather(*[self.live.checkpoint() for _ in range(4)])
        self.assertEqual(asyncio.run(lanes()), [False] * 4)
        paused_lines = [x for x in self.live.snapshot()["log"] if x["msg"].startswith("Paused.")]
        self.assertEqual(len(paused_lines), 1)                     # said once, not once per lane

    def test_long_pause_stops_the_run(self):
        self.live.PAUSE_LIMIT_S = 0.4
        self.live.pause()
        self.assertTrue(asyncio.run(self.live.checkpoint()))
        self.assertTrue(self.live.cancelled())

    def test_cannot_pause_when_idle(self):
        idle = SA.LiveState()
        self.assertFalse(idle.pause())
        self.assertFalse(idle.resume())

    def test_whatsapp_pauses_between_chats(self):
        wa = W._State()
        self.assertFalse(wa.pause())                               # nothing running
        self.assertTrue(wa.start("send"))
        self.assertTrue(wa.pause())
        later(0.4, wa.resume)
        t0 = time.time()
        asyncio.run(wa.checkpoint())
        self.assertGreaterEqual(time.time() - t0, 0.3)
        self.assertEqual(wa.snapshot().get("pause"), "")

    def test_running_now_pause_and_resume(self):
        import running_now
        import loading_automation as LA
        LA.LIVE.reset(kind="prepare")
        try:
            self.assertTrue(running_now.pause("loadings")["ok"])
            snap = next(p for p in running_now.running() if p["id"] == "loadings")
            self.assertEqual(snap["pause"], "pausing")
            self.assertTrue(running_now.resume("loadings")["ok"])
            self.assertFalse(running_now.resume("loadings")["ok"])
            self.assertFalse(running_now.pause("nope")["ok"])
        finally:
            LA.LIVE.set(active=False)


if __name__ == "__main__":
    unittest.main()
