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
    def __init__(self, model_dir="D:/czb/models/easyocr"):
        import easyocr
        self.ocr = easyocr.Reader(["en"], gpu=True, model_storage_directory=model_dir, verbose=False)
        self.points_hist = deque(maxlen=3)
        self.points = None

    def _read(self, frame, box, allow, scale=2.0):
        x1, y1, x2, y2 = box
        crop = frame[y1:y2, x1:x2]
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        res = self.ocr.readtext(crop, allowlist=allow, detail=1, paragraph=False)
        return [(t, c) for _, t, c in res]

    def read_points(self, frame):
        """Smoothed points total, or the last good value if OCR misses."""
        res = [(t, c) for t, c in self._read(frame, POINTS_BOX, "0123456789") if c > 0.3 and t.isdigit()]
        if res:
            val = int(max(res, key=lambda r: len(r[0]))[0])
            # reject implausible jumps (OCR merging digits); points rarely jump > 2000 between reads
            if self.points is None or abs(val - self.points) <= 2000 or (self.points_hist and val == self.points_hist[-1]):
                self.points_hist.append(val)
        if self.points_hist:
            self.points = max(self.points_hist) if len(self.points_hist) == 3 else self.points_hist[-1]
        return self.points

    def read_ammo(self, frame):
        """(mag, reserve) or None when the ammo counter is faded out."""
        txt = " ".join(t for t, c in self._read(frame, AMMO_BOX, "0123456789/ ") if c > 0.2)
        m = re.match(r"\s*(\d+)\s*/\s*(\d+)", txt)
        return (int(m.group(1)), int(m.group(2))) if m else None

    def game_over(self, frame):
        txt = " ".join(t for t, c in self._read(frame, GAME_OVER_BOX, None, scale=1.0)).upper()
        return "GAME OVER" in txt or "SURVIVED" in txt or ("KILLS" in txt and "HEADSHOTS" in txt)
