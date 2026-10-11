"""Read the port's HUD from 1280x720 frames: points, ammo, GAME OVER. easyocr on GPU, fixed crops.

Crops were measured on vel.gg/bo1z Kino at 1280x720 (plan §5.3: max of last 3 reads, reject big jumps).
"""
import re
from collections import deque

import cv2

# (x1, y1, x2, y2) in 1280x720 pixels
POINTS_BOX = (1126, 568, 1270, 612)     # "533" next to the blood splat; "+10" pop-ups sit left of x=1126
AMMO_BOX = (1075, 672, 1170, 712)       # "7 / 16", fades when idle
GAME_OVER_BOX = (250, 140, 1030, 330)   # "GAME OVER / You Survived N Rounds", then the scoreboard


class HudReader:
    def __init__(self, model_dir=None, gpu=True):
        import easyocr
        from bot.paths import p as _p
        model_dir = model_dir or _p("models", "easyocr")
        import os
        os.makedirs(model_dir, exist_ok=True)
        self.ocr = easyocr.Reader(["en"], gpu=gpu, model_storage_directory=model_dir, verbose=False)
        self.points_hist = deque(maxlen=3)
        self.points = None

    def _read(self, frame, box, allow, scale=2.0):
        x1, y1, x2, y2 = box
        crop = frame[y1:y2, x1:x2]
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        res = self.ocr.readtext(crop, allowlist=allow, detail=1, paragraph=False)
        return [(t, c) for _, t, c in res]

    def read_points(self, frame):
        """Confirmed points total: a value is accepted only after two identical consecutive reads,
        and the total never decreases (the bot never spends). Returns the last confirmed value."""
        res = [(t, c) for t, c in self._read(frame, POINTS_BOX, "0123456789") if c > 0.5 and t.isdigit()]
        val = int(max(res, key=lambda r: len(r[0]))[0]) if res else None
        if val is not None and self.points_hist and val == self.points_hist[-1]:
            if self.points is None or (self.points <= val <= self.points + 2000):
                self.points = val
        self.points_hist.append(val)
        return self.points

    def read_ammo(self, frame):
        """(mag, reserve) or None when the ammo counter is faded out."""
        txt = " ".join(t for t, c in self._read(frame, AMMO_BOX, "0123456789/ ") if c > 0.2)
        m = re.match(r"\s*(\d+)\s*/\s*(\d+)", txt)
        return (int(m.group(1)), int(m.group(2))) if m else None

    def scoreboard(self, frame):
        """Post-game table 'Player  <points> <kills> <headshots>' -> dict, or None if not visible yet."""
        # header row sits ~y 270-290 (720p) and the values row ~y 300-320; read the band and pick digits
        band = frame[260:340, 700:1050]
        band = cv2.resize(band, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        items = self.ocr.readtext(band, detail=1, paragraph=False)
        words = " ".join(t for _, t, _ in items).upper()
        nums = sorted(((b[0][0], t) for b, t, c in items if t.strip().isdigit()), key=lambda x: x[0])
        if "KILLS" not in words or len(nums) < 3:
            return None
        pts, kills, heads = (int(t) for _, t in nums[:3])
        return {"points": pts, "kills": kills, "headshots": heads}

    def game_over(self, frame):
        txt = " ".join(t for t, c in self._read(frame, GAME_OVER_BOX, None, scale=1.0)).upper()
        return "GAME OVER" in txt or "SURVIVED" in txt or ("KILLS" in txt and "HEADSHOTS" in txt)
