"""Tactical HUD renderer for the on-device vision stream. Pure OpenCV + cached PIL text, ~3 ms/frame at 720p.

hud = HUD(); out = hud.render(frame, tracks, target, stats={...}, info={...})
"""
import math
import os
from collections import deque

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# BGR
ACCENT = (255, 214, 64)
WARN = (40, 186, 255)
DANGER = (70, 60, 255)
WHITE = (238, 238, 238)
DIM = (150, 150, 150)
DARK = (18, 14, 10)

FONT_DIR = "C:/Windows/Fonts"
FONT_UI = os.path.join(FONT_DIR, "bahnschrift.ttf")
FONT_MONO = os.path.join(FONT_DIR, "consolab.ttf")

HFOV_DEG = 90.0  # approximate horizontal FOV for the radar bearing
ZOMBIE_HEIGHT_M = 1.8
VFOV_DEG = 59.0


def est_distance_m(box_h, frame_h):
    frac = max(box_h / frame_h, 1e-3)
    return ZOMBIE_HEIGHT_M / (2 * math.tan(math.radians(VFOV_DEG / 2)) * frac)


def threat_color(prox):
    """prox = box_h / frame_h. Blend accent -> amber -> red."""
    def mix(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    if prox < 0.22:
        return ACCENT
    if prox < 0.42:
        return mix(ACCENT, WARN, (prox - 0.22) / 0.20)
    return mix(WARN, DANGER, min(1.0, (prox - 0.42) / 0.18))


class TextCache:
    def __init__(self, max_items=3000):
        self.cache = {}
        self.max_items = max_items
        self.fonts = {}

    def font(self, path, size):
        key = (path, size)
        if key not in self.fonts:
            try:
                self.fonts[key] = ImageFont.truetype(path, size)
            except OSError:
                self.fonts[key] = ImageFont.load_default()
        return self.fonts[key]

    def get(self, text, size, color, mono=False):
        key = (text, size, color, mono)
        patch = self.cache.get(key)
        if patch is None:
            f = self.font(FONT_MONO if mono else FONT_UI, size)
            l, t, r, b = f.getbbox(text)
            w, h = max(1, r - l + 2), max(1, b - t + 2)
            img = Image.new("L", (w, h), 0)
            ImageDraw.Draw(img).text((-l + 1, -t + 1), text, font=f, fill=255)
            alpha = np.asarray(img, np.float32)[..., None] / 255.0
            patch = (alpha, np.array(color, np.float32))
            if len(self.cache) > self.max_items:
                self.cache.clear()
            self.cache[key] = patch
        return patch

    def put(self, img, text, org, size=16, color=WHITE, mono=False, anchor="lt", shadow=True):
        alpha, col = self.get(text, size, color, mono)
        h, w = alpha.shape[:2]
        x, y = org
        if anchor[0] == "m":
            x -= w // 2
        elif anchor[0] == "r":
            x -= w
        if anchor[1] == "m":
            y -= h // 2
        elif anchor[1] == "b":
            y -= h
        if shadow:
            self._blend(img, alpha * 0.8, np.zeros(3, np.float32), x + 1, y + 1)
        self._blend(img, alpha, col, x, y)
        return w, h

    @staticmethod
    def _blend(img, alpha, col, x, y):
        H, W = img.shape[:2]
        h, w = alpha.shape[:2]
        x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
        if x0 >= x1 or y0 >= y1:
            return
        a = alpha[y0 - y:y1 - y, x0 - x:x1 - x]
        roi = img[y0:y1, x0:x1].astype(np.float32)
        img[y0:y1, x0:x1] = (roi * (1 - a) + col * a).astype(np.uint8)


class HUD:
    def __init__(self, title="ZOMBIE VISION", subtitle="ON-DEVICE // YOLO11n", grade=True):
        self.title = title
        self.subtitle = subtitle
        self.grade = grade
        self.text = TextCache()
        self.t = 0
        self.fps_hist = deque(maxlen=90)
        self._grade_cache = {}

    # ---------- base look ----------
    def _grade_mask(self, H, W):
        key = (H, W)
        if key not in self._grade_cache:
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
            vign = np.clip(1.08 - 0.42 * r ** 2, 0.45, 1.0)
            scan = np.where((yy.astype(int) % 3) == 0, 0.9, 1.0)
            m = vign * scan
            # slight cool tint: B up, R down
            g = np.stack([m * 1.0, m * 0.96, m * 0.9], -1)
            self._grade_cache[key] = np.clip(g * 255, 0, 255).astype(np.uint8)
        return self._grade_cache[key]

    # ---------- primitives ----------
    @staticmethod
    def brackets(img, x1, y1, x2, y2, color, thick=2, frac=0.25):
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        L = max(6, int(frac * min(x2 - x1, y2 - y1)))
        for (cx, cy, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            cv2.line(img, (cx, cy), (cx + dx * L, cy), color, thick, cv2.LINE_AA)
            cv2.line(img, (cx, cy), (cx, cy + dy * L), color, thick, cv2.LINE_AA)

    @staticmethod
    def dashed_line(img, p0, p1, color, dash=8, gap=6, thick=1):
        x0, y0 = p0
        x1, y1 = p1
        d = math.hypot(x1 - x0, y1 - y0)
        if d < 1:
            return
        ux, uy = (x1 - x0) / d, (y1 - y0) / d
        s = 0.0
        while s < d:
            e = min(s + dash, d)
            cv2.line(img, (int(x0 + ux * s), int(y0 + uy * s)), (int(x0 + ux * e), int(y0 + uy * e)), color, thick, cv2.LINE_AA)
            s = e + gap

    def _fills(self, img, tracks, info, H, W, S):
        """All translucent fills in one layer, blended once, so strokes and text stay crisp on top."""
        if tracks:  # light tint inside boxes, separate from the darker panels
            tint = img.copy()
            for tr in tracks:
                b = tr.box
                cv2.rectangle(tint, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), threat_color(b.h / H), -1)
            cv2.addWeighted(tint, 0.14, img, 0.86, 0, img)
        ov = img.copy()
        for tr in tracks:
            b = tr.box
            label = self._label(tr, H)
            alpha, _ = self.text.get(label, int(14 * S), WHITE, True)
            lx, ly = int(b.x1), int(b.y1) - int(22 * S)
            cv2.rectangle(ov, (lx, ly), (lx + alpha.shape[1] + int(12 * S), ly + int(19 * S)), DARK, -1)
        cv2.rectangle(ov, (int(16 * S), int(16 * S)), (int(316 * S), int(166 * S)), DARK, -1)
        R = int(80 * S)
        half = HFOV_DEG / 2
        cv2.ellipse(ov, (W - int(24 * S) - R, int(30 * S) + R), (R, R), 0, -90 - half, -90 + half, (60, 50, 30), -1, cv2.LINE_AA)
        n = sum(k in info for k in ("generation", "points")) + bool(info.get("strategy"))
        if n:
            by = H - int(16 * S) - int(22 * S) * n - int(10 * S)
            cv2.rectangle(ov, (int(16 * S), by), (int(396 * S), by + int(22 * S) * n + int(10 * S)), DARK, -1)
        cv2.addWeighted(ov, 0.42, img, 0.58, 0, img)

    @staticmethod
    def _label(tr, H):
        b = tr.box
        return f"Z-{tr.id:02d}  {int(b.conf * 100)}%  {est_distance_m(b.h, H):4.1f}m"

    # ---------- main ----------
    def render(self, frame, tracks, target=None, lock_frames=0, stats=None, info=None,
               aim_y_offset=0.2, fire_radius_px=None, retreat_prox=0.45, masks=()):
        """tracks: list of tracker.Track (or anything with .id, .box, .vx, .vy, .trail).
        stats: dict(fps, infer_ms, device, model). info: dict(action, generation, persona, strategy, points)."""
        self.t += 1
        stats = stats or {}
        info = info or {}
        H, W = frame.shape[:2]
        S = H / 720.0  # scale UI with resolution
        img = cv2.multiply(frame, self._grade_mask(H, W), scale=1 / 255.0) if self.grade else frame.copy()
        cx, cy = W // 2, H // 2
        self._fills(img, tracks, info, H, W, S)

        for x1, y1, x2, y2 in masks:
            cv2.rectangle(img, (int(x1 * W), int(y1 * H)), (int(x2 * W), int(y2 * H)), (70, 70, 70), 1, cv2.LINE_AA)

        # --- tracks ---
        max_prox = 0.0
        for tr in tracks:
            b = tr.box
            prox = b.h / H
            max_prox = max(max_prox, prox)
            col = threat_color(prox)
            is_tgt = target is not None and tr.id == target.id
            # trail
            if len(tr.trail) > 1:
                for i in range(1, len(tr.trail)):
                    a = i / len(tr.trail)
                    p0 = tuple(int(v) for v in tr.trail[i - 1])
                    p1 = tuple(int(v) for v in tr.trail[i])
                    if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 0.08 * W:
                        continue  # box resize jump, not motion
                    cv2.line(img, p0, p1, tuple(int(c * a) for c in col), 1, cv2.LINE_AA)
            self.brackets(img, b.x1, b.y1, b.x2, b.y2, col, 2 if not is_tgt else 3)
            # velocity vector
            sp = math.hypot(tr.vx, tr.vy)
            if 1.5 < sp and prox < 0.35:
                k = min(6.0, 60 * S / sp)  # cap arrow length
                cv2.arrowedLine(img, (int(b.cx), int(b.cy)), (int(b.cx + tr.vx * k), int(b.cy + tr.vy * k)),
                                col, 1, cv2.LINE_AA, tipLength=0.3)
            # label pill
            label = self._label(tr, H)
            lx, ly = int(b.x1), int(b.y1) - int(22 * S)
            cv2.rectangle(img, (lx, ly), (lx + int(3 * S), ly + int(19 * S)), col, -1)
            self.text.put(img, label, (lx + int(8 * S), ly + int(3 * S)), int(14 * S), WHITE, mono=True, shadow=False)

        # --- target lock ---
        if target is not None:
            b = target.box
            ax, ay = int(b.cx), int(b.y1 + aim_y_offset * b.h)
            col = DANGER if lock_frames >= 4 else WARN
            r = int(max(16 * S, 0.22 * b.w))
            spin = (self.t * 7) % 360
            for k in range(3):
                st = spin + k * 120
                cv2.ellipse(img, (ax, ay), (r, r), 0, st, st + 70, col, 2, cv2.LINE_AA)
            cv2.ellipse(img, (ax, ay), (r + int(7 * S), r + int(7 * S)), 0, -spin, -spin + 40, col, 1, cv2.LINE_AA)
            d = int(5 * S)
            cv2.line(img, (ax - d, ay), (ax + d, ay), col, 1, cv2.LINE_AA)
            cv2.line(img, (ax, ay - d), (ax, ay + d), col, 1, cv2.LINE_AA)
            self.dashed_line(img, (cx, cy), (ax, ay), col)
            err = math.hypot(ax - cx, ay - cy)
            locked = lock_frames >= 4
            tag = "LOCKED" if locked else "ACQUIRING"
            if fire_radius_px is not None and err < fire_radius_px:
                tag = "FIRE"
                col = DANGER
            self.text.put(img, f"{tag}  Z-{target.id:02d}", (ax + r + int(10 * S), ay - int(10 * S)), int(18 * S), col)
            self.text.put(img, f"ERR {err:5.0f}px", (ax + r + int(10 * S), ay + int(10 * S)), int(13 * S), WHITE, mono=True)

        # --- crosshair ---
        g, L = int(6 * S), int(14 * S)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            cv2.line(img, (cx + dx * g, cy + dy * g), (cx + dx * (g + L), cy + dy * (g + L)), WHITE, 1, cv2.LINE_AA)
        if fire_radius_px:
            cv2.circle(img, (cx, cy), int(fire_radius_px), (200, 200, 200), 1, cv2.LINE_AA)

        # --- top-left telemetry panel ---
        px, py, pw, ph = int(16 * S), int(16 * S), int(300 * S), int(150 * S)
        cv2.rectangle(img, (px, py), (px + int(3 * S), py + ph), ACCENT, -1)
        self.text.put(img, self.title, (px + int(14 * S), py + int(10 * S)), int(22 * S), ACCENT)
        self.text.put(img, self.subtitle, (px + int(14 * S), py + int(38 * S)), int(13 * S), DIM)
        fps = stats.get("fps", 0.0)
        self.fps_hist.append(fps)
        rows = [
            ("FPS", f"{fps:5.1f}"),
            ("LATENCY", f"{stats.get('infer_ms', 0):5.1f} ms"),
            ("DEVICE", stats.get("device", "-")),
            ("TRACKS", f"{len(tracks)}"),
        ]
        for i, (k, v) in enumerate(rows):
            yy = py + int((60 + i * 20) * S)
            self.text.put(img, k, (px + int(14 * S), yy), int(13 * S), DIM, mono=True)
            self.text.put(img, v, (px + int(100 * S), yy), int(13 * S), WHITE, mono=True)
        # fps sparkline
        if len(self.fps_hist) > 2:
            sx0, sy0, sw, sh = px + int(200 * S), py + int(64 * S), int(88 * S), int(30 * S)
            mx = max(60.0, max(self.fps_hist))
            pts = np.array([[sx0 + int(i * sw / (self.fps_hist.maxlen - 1)), sy0 + sh - int(v / mx * sh)]
                            for i, v in enumerate(self.fps_hist)], np.int32)
            cv2.polylines(img, [pts], False, ACCENT, 1, cv2.LINE_AA)
            cv2.line(img, (sx0, sy0 + sh), (sx0 + sw, sy0 + sh), DIM, 1)

        # --- top-center threat meter + action ---
        segs, sw, sg = 16, int(16 * S), int(3 * S)
        tw = segs * (sw + sg)
        tx, ty = cx - tw // 2, int(22 * S)
        lvl = min(1.0, max_prox / max(retreat_prox, 1e-3))
        on = int(round(lvl * segs))
        for i in range(segs):
            c = threat_color(retreat_prox * (i + 1) / segs) if i < on else (60, 60, 60)
            cv2.rectangle(img, (tx + i * (sw + sg), ty), (tx + i * (sw + sg) + sw, ty + int(8 * S)), c, -1)
        self.text.put(img, "THREAT", (tx - int(8 * S), ty - int(2 * S)), int(13 * S), DIM, anchor="rt")
        if max_prox >= retreat_prox and (self.t // 6) % 2 == 0:
            self.text.put(img, "DANGER CLOSE", (cx, ty + int(16 * S)), int(22 * S), DANGER, anchor="mt")
        action = info.get("action")
        if action:
            acol = {"ENGAGE": DANGER, "RETREAT": WARN, "SCAN": ACCENT, "RELOAD": WARN}.get(action, WHITE)
            self.text.put(img, action, (cx, ty + int(44 * S)), int(18 * S), acol, anchor="mt")

        # --- top-right radar ---
        R = int(80 * S)
        rcx, rcy = W - int(24 * S) - R, int(30 * S) + R
        half = HFOV_DEG / 2
        for rr in (R // 3, 2 * R // 3, R):
            cv2.ellipse(img, (rcx, rcy), (rr, rr), 0, -90 - half, -90 + half, (110, 100, 70), 1, cv2.LINE_AA)
        for a in (-half, half):
            ang = math.radians(-90 + a)
            cv2.line(img, (rcx, rcy), (int(rcx + R * math.cos(ang)), int(rcy + R * math.sin(ang))), (110, 100, 70), 1, cv2.LINE_AA)
        sweep = -half + (self.t * 3) % int(2 * half)
        ang = math.radians(-90 + sweep)
        cv2.line(img, (rcx, rcy), (int(rcx + R * math.cos(ang)), int(rcy + R * math.sin(ang))), ACCENT, 1, cv2.LINE_AA)
        for tr in tracks:
            b = tr.box
            bearing = (b.cx / W - 0.5) * HFOV_DEG
            rng = min(1.0, est_distance_m(b.h, H) / 15.0) * R
            ang = math.radians(-90 + bearing)
            p = (int(rcx + rng * math.cos(ang)), int(rcy + rng * math.sin(ang)))
            col = threat_color(b.h / H)
            cv2.circle(img, p, int(4 * S), col, -1, cv2.LINE_AA)
            if target is not None and tr.id == target.id:
                cv2.circle(img, p, int(8 * S), DANGER, 1, cv2.LINE_AA)
        cv2.circle(img, (rcx, rcy), int(3 * S), WHITE, -1, cv2.LINE_AA)
        self.text.put(img, "RADAR  15m", (rcx, rcy + int(8 * S)), int(12 * S), DIM, mono=True, anchor="mt")

        # --- bottom-left: agent / strategy strip ---
        lines = []
        if "generation" in info:
            lines.append(f"GEN {info['generation']:02d}  {info.get('persona', '')}".strip())
        if info.get("strategy"):
            lines.append(info["strategy"])
        if "points" in info:
            lines.append(f"POINTS {info['points']}")
        if lines:
            bx, by = int(16 * S), H - int(16 * S) - int(22 * S) * len(lines) - int(10 * S)
            cv2.rectangle(img, (bx, by), (bx + int(3 * S), by + int(22 * S) * len(lines) + int(10 * S)), ACCENT, -1)
            for i, s in enumerate(lines):
                self.text.put(img, s, (bx + int(12 * S), by + int(6 * S) + i * int(22 * S)), int(15 * S),
                              WHITE if i else ACCENT, mono=i > 0)

        return img
