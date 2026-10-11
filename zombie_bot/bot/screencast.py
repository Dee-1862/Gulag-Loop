"""Frames straight from a page's compositor (CDP Page.startScreencast), not from the screen.

Desktop capture only sees what's on top: in parallel mode another window covering a game tile meant the bot
was "watching" the editor. The screencast delivers the page's own frames regardless of occlusion.
"""
import time

import cv2
import numpy as np


class ScreencastCapture:
    def __init__(self, page, max_w=1280, max_h=720, quality=80):
        self.page = page
        self.frame = None
        self.seq = 0
        self._last_seq = -1
        self.backend = "screencast"
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._on_frame)
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": quality, "maxWidth": max_w,
                                              "maxHeight": max_h, "everyNthFrame": 1})

    def _on_frame(self, ev):
        import base64
        try:
            self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:
            pass
        buf = np.frombuffer(base64.b64decode(ev["data"]), np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is not None:
            self.frame = img
            self.seq += 1

    def grab(self, timeout=0.25):
        """Latest frame; waits (pumping Playwright events) for a new one up to `timeout`."""
        t0 = time.time()
        while self.seq == self._last_seq and time.time() - t0 < timeout:
            self.page.wait_for_timeout(2)  # lets the sync API dispatch screencast events
        while self.frame is None:
            self.page.wait_for_timeout(10)
        self._last_seq = self.seq
        return self.frame

    def close(self):
        try:
            self.cdp.send("Page.stopScreencast")
        except Exception:
            pass
