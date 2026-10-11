"""Situational awareness for the bot: where it is facing, which zombies are around, where they come from.

The bot only sees what is in front of it. This module gives it:
- Heading: yaw/pitch from the mouse counts it sends (calibrated counts_per_px, ~90 deg horizontal FOV).
- ZombieMemory: zombies that left the screen are remembered by bearing for a few seconds, so the bot
  knows how many are active and turns toward the nearest instead of sweeping blindly.
- DamageSensor: the red damage flash at the screen edges means it is being hit, usually from behind.
- EntryPoints: bearings where zombies first appear (windows/barricades), learned across episodes and
  stored in MongoDB per map, so the bot watches entry points instead of empty walls.
- RecoilTracker: measures how far each shot kicks the view up and compensates on the next shots.
"""
import math

import cv2
import numpy as np

HFOV_DEG = 90.0
FRAME_W, FRAME_H = 1280, 720


def wrap180(deg):
    return (deg + 180.0) % 360.0 - 180.0


class Heading:
    def __init__(self, counts_per_px):
        self.deg_per_px = HFOV_DEG / FRAME_W
        self.deg_per_count = self.deg_per_px / max(counts_per_px, 1e-6)
        self.yaw = 0.0           # degrees, 0 = facing at episode start
        self.pitch_counts = 0    # + = looking down, 0 = start (level)

    def on_move(self, dx, dy):
        self.yaw = (self.yaw + int(dx) * self.deg_per_count) % 360.0
        self.pitch_counts += int(dy)

    def bearing_of(self, x_px):
        return (self.yaw + (x_px - FRAME_W / 2) * self.deg_per_px) % 360.0

    def counts_to(self, bearing):
        """Mouse counts (signed) that turn the view to face `bearing`."""
        return wrap180(bearing - self.yaw) / self.deg_per_count


class ZombieMemory:
    def __init__(self, ttl_s=6.0):
        self.ttl = ttl_s
        self.items = {}          # track id -> {bearing, h, first, last}
        self.max_active = 0
        self.active_sum = self.active_n = 0
        self.new_ids = []        # ids seen for the first time this frame

    def update(self, tracks, heading, now):
        self.new_ids = []
        for t in tracks:
            it = self.items.get(t.id)
            if it is None:
                self.new_ids.append(t.id)
                it = self.items[t.id] = {"first": now}
            it.update(bearing=heading.bearing_of(t.box.cx), h=t.box.h / FRAME_H, last=now)
        for tid in [k for k, v in self.items.items() if now - v["last"] > self.ttl]:
            del self.items[tid]
        n = len(self.items)
        self.max_active = max(self.max_active, n)
        self.active_sum += n
        self.active_n += 1
        return n

    def offscreen(self, now, visible_ids):
        """Remembered zombies not on screen now, closest (tallest last box) first."""
        return sorted((v for k, v in self.items.items() if k not in visible_ids and now - v["last"] < 3.0),
                      key=lambda v: -v["h"])

    @property
    def avg_active(self):
        return self.active_sum / self.active_n if self.active_n else 0.0


class DamageSensor:
    """Red flash at the screen edges when the player is hit. Compared with a running baseline so red
    scenery (banners, blood decals) does not trigger it."""

    def __init__(self, border=0.12, jump=0.06, floor=0.10, cooldown_s=0.8):
        self.border, self.jump, self.floor, self.cooldown = border, jump, floor, cooldown_s
        self.base = None
        self.last_hit = -1e9
        self.events = 0

    def _red_border_frac(self, frame):
        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA).astype(np.int16)
        b, g, r = small[..., 0], small[..., 1], small[..., 2]
        red = (r > 90) & (r > 1.7 * g) & (r > 1.7 * b)
        bw, bh = int(160 * self.border), int(90 * self.border)
        mask = np.ones((90, 160), bool)
        mask[bh:90 - bh, bw:160 - bw] = False
        return float(red[mask].mean())

    def update(self, frame, now):
        f = self._red_border_frac(frame)
        if self.base is None:
            self.base = f
        hit = f > self.floor and f > self.base + self.jump and now - self.last_hit > self.cooldown
        if hit:
            self.last_hit = now
            self.events += 1
        else:
            self.base = 0.95 * self.base + 0.05 * f  # only learn the baseline when not being hit
        return hit

    def recently_hit(self, now, within_s=1.0):
        return now - self.last_hit < within_s


class EntryPoints:
    """Bearings where zombies first appear. Loaded from and saved to MongoDB (collection entry_events)."""

    BIN_DEG = 15

    def __init__(self, map_name, db=None):
        self.map = map_name
        self.db = db
        self.points = []         # [(bearing, count)], most frequent first
        self.events = []
        self.cursor = 0
        self.dwell_until = 0.0

    def load(self, min_count=2, limit=6):
        if self.db is None:
            return self.points
        rows = self.db["entry_events"].aggregate([
            {"$match": {"map": self.map, "kind": "first_seen"}},
            {"$bucket": {"groupBy": "$bearing", "boundaries": list(range(0, 361, self.BIN_DEG)),
                         "default": "other", "output": {"count": {"$sum": 1}, "mean": {"$avg": "$bearing"}}}},
            {"$match": {"count": {"$gte": min_count}}},
            {"$sort": {"count": -1}},
            {"$limit": limit},
        ])
        self.points = [(r["mean"], r["count"]) for r in rows]
        return self.points

    def record(self, bearing, kind, now):
        self.events.append({"map": self.map, "bearing": round(bearing % 360.0, 1), "kind": kind, "t": round(now, 2)})

    def save(self, experiment_id=None, job_id=None):
        if self.db is None or not self.events:
            return 0
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc)
        self.db["entry_events"].insert_many([{**e, "experiment_id": experiment_id, "job_id": job_id, "ts": ts}
                                             for e in self.events])
        return len(self.events)

    def next_watch(self, heading, now, dwell_s=1.2):
        """Bearing to watch next: entry points in turn, nearest first, holding each for dwell_s."""
        if not self.points:
            return None
        if now >= self.dwell_until:
            order = sorted(range(len(self.points)), key=lambda i: abs(wrap180(self.points[i][0] - heading.yaw)))
            self.cursor = order[1] if len(order) > 1 and order[0] == self.cursor else order[0]
            self.dwell_until = now + dwell_s
        return self.points[self.cursor][0]


class RecoilTracker:
    """Learns how far one shot kicks the view (the target drops on screen right after firing) and
    returns the mouse counts to pull down after each shot."""

    def __init__(self, counts_per_px, gain=0.8, max_counts=30):
        self.cpp, self.gain, self.max_counts = counts_per_px, gain, max_counts
        self.pending = None      # (time, target id, err_y before the shot)
        self.jump_ema = None
        self.samples = 0

    def on_fire(self, now, target_id, err_y):
        self.pending = (now, target_id, err_y)

    def observe(self, now, target_id, err_y):
        if self.pending is None:
            return
        t, tid, before = self.pending
        if now - t > 0.3:
            self.pending = None
            return
        if tid != target_id:
            return
        if now - t >= 0.08:      # a couple of frames after the shot
            jump = err_y - before  # + = target moved down on screen = view kicked up
            if abs(jump) < 120:
                self.jump_ema = jump if self.jump_ema is None else 0.8 * self.jump_ema + 0.2 * jump
                self.samples += 1
            self.pending = None

    def comp_counts(self):
        if not self.jump_ema or self.jump_ema <= 0:
            return 0
        return int(min(self.max_counts, self.gain * self.jump_ema * self.cpp))


def draw_compass(img, heading, memory, entries, active, hit_flash, now):
    """Strip across the top: what is around the bot, relative to where it faces."""
    H, W = img.shape[:2]
    y0, h = int(0.12 * H), int(0.04 * H)
    overlay = img.copy()
    cv2.rectangle(overlay, (int(0.2 * W), y0), (int(0.8 * W), y0 + h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
    x_of = lambda bearing: int(W / 2 + wrap180(bearing - heading.yaw) / 180.0 * 0.3 * W)
    cv2.line(img, (W // 2, y0), (W // 2, y0 + h), (238, 238, 238), 1, cv2.LINE_AA)
    for b, count in entries.points:
        x = x_of(b)
        cv2.line(img, (x, y0 + 2), (x, y0 + h - 2), (0, 200, 255), 2, cv2.LINE_AA)  # entry point (amber)
    for v in memory.items.values():
        x = x_of(v["bearing"])
        r = max(4, int(6 + 14 * v["h"]))
        pts = np.array([[x, y0 + h - 3], [x - r // 2, y0 + 4], [x + r // 2, y0 + 4]], np.int32)
        cv2.fillPoly(img, [pts], (40, 40, 230))                                            # zombie (red)
    label = f"ACTIVE ZOMBIES {active}   ENTRY POINTS {len(entries.points)}   HEADING {heading.yaw:5.0f}"
    cv2.putText(img, label, (int(0.2 * W) + 6, y0 + h + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (238, 238, 238), 1,
                cv2.LINE_AA)
    if hit_flash:
        cv2.putText(img, "HIT - TURNING", (int(0.8 * W) - 150, y0 + h + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (40, 40, 255), 2, cv2.LINE_AA)
    return img
