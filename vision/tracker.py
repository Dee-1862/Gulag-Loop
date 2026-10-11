"""Tiny IoU tracker: stable zombie IDs across frames, velocity, and a sticky lock.

Without this, the chosen target can flip between two zombies frame to frame, which looks like
aim overshoot in telemetry and makes the evolution agents chase a fake problem.
"""
import math

from vision.detector import Box


def iou(a, b):
    ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


class Track:
    def __init__(self, tid, box):
        self.id = tid
        self.box = box
        self.hits = 1
        self.missed = 0
        self.age = 0
        self.vx = self.vy = 0.0  # px/frame, EMA
        self.trail = [(box.cx, box.cy)]

    @property
    def confirmed(self):
        return self.hits >= 2

    def update(self, box):
        a = 0.5
        self.vx = (1 - a) * self.vx + a * (box.cx - self.box.cx)
        self.vy = (1 - a) * self.vy + a * (box.cy - self.box.cy)
        self.box = box
        self.hits += 1
        self.missed = 0
        self.trail.append((box.cx, box.cy))
        del self.trail[:-12]

    def predict(self):
        """Coast through a missed detection by sliding the box along its velocity."""
        b = self.box
        self.box = Box(b.x1 + self.vx, b.y1 + self.vy, b.x2 + self.vx, b.y2 + self.vy, b.conf * 0.9)
        self.missed += 1


class Tracker:
    def __init__(self, iou_thresh=0.2, max_missed=6, dist_frac=0.12):
        self.iou_thresh = iou_thresh
        self.max_missed = max_missed
        self.dist_frac = dist_frac
        self.tracks = []
        self.next_id = 1
        self.locked_id = None
        self.lock_frames = 0

    def update(self, boxes, frame_w):
        for t in self.tracks:
            t.age += 1
        # greedy match by IoU, falling back to center distance for fast-moving small boxes
        pairs = []
        for ti, t in enumerate(self.tracks):
            for bi, b in enumerate(boxes):
                s = iou(t.box, b)
                if s < self.iou_thresh:
                    d = math.hypot(t.box.cx + t.vx - b.cx, t.box.cy + t.vy - b.cy)
                    if d > self.dist_frac * frame_w:
                        continue
                    s = 0.01 * (1 - d / (self.dist_frac * frame_w))
                pairs.append((s, ti, bi))
        pairs.sort(reverse=True)
        used_t, used_b = set(), set()
        for s, ti, bi in pairs:
            if ti in used_t or bi in used_b:
                continue
            self.tracks[ti].update(boxes[bi])
            used_t.add(ti)
            used_b.add(bi)
        for ti, t in enumerate(self.tracks):
            if ti not in used_t:
                t.predict()
        for bi, b in enumerate(boxes):
            if bi not in used_b:
                self.tracks.append(Track(self.next_id, b))
                self.next_id += 1
        self.tracks = [t for t in self.tracks if t.missed <= self.max_missed]
        return self.active()

    def active(self):
        return [t for t in self.tracks if t.confirmed and t.missed <= 2]

    def select(self, rule, crosshair, switch_margin=1.3):
        """Pick a target with hysteresis: only abandon the current lock if another zombie
        scores switch_margin x better (or the locked one is gone)."""
        tracks = self.active()
        if not tracks:
            self.locked_id, self.lock_frames = None, 0
            return None
        cx, cy = crosshair
        if rule == "nearest":
            score = lambda t: t.box.h
        elif rule == "largest":
            score = lambda t: t.box.w * t.box.h
        else:
            score = lambda t: 1.0 / (1.0 + math.hypot(t.box.cx - cx, t.box.cy - cy))
        best = max(tracks, key=score)
        cur = next((t for t in tracks if t.id == self.locked_id), None)
        if cur is not None and score(best) < switch_margin * score(cur):
            best = cur
        if best.id == self.locked_id:
            self.lock_frames += 1
        else:
            self.locked_id, self.lock_frames = best.id, 1
        return best
