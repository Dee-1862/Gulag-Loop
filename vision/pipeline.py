"""One-call vision interface for controller.py.

    from vision.pipeline import VisionPipeline
    vp = VisionPipeline.from_config("config.yaml")          # live capture of the game window
    while running:
        obs = vp.step(S.det_conf, S.target_rule, S.aim_y_offset)
        if obs.target: move_mouse(obs.err_x * S.aim_gain, ...)
        vp.hud_frame(info={"action": "ENGAGE", "generation": 3})  # optional, for the video
"""
from dataclasses import dataclass, field
import os
import time

import yaml

from vision.capture import Capture, find_window_region
from vision.detector import Detector
from vision.hud import HUD
from vision.tracker import Tracker


@dataclass
class Observation:
    t: float
    frame: object
    tracks: list
    target: object                 # tracker.Track or None
    lock_frames: int
    err_x: float = 0.0             # aim point - crosshair, px (+ = right)
    err_y: float = 0.0             # (+ = down)
    target_h_frac: float = 0.0     # target box height / frame height (proximity)
    nearest_h_frac: float = 0.0    # tallest box / frame height (for close_calls)
    infer_ms: float = 0.0
    telemetry: dict = field(default_factory=dict)


class VisionPipeline:
    def __init__(self, detector, capture=None, hud=None, masks=()):
        self.det = detector
        self.cap = capture
        self.tracker = Tracker()
        self.hud = hud or HUD()
        self.masks = masks
        self.last = None
        self.fps = 0.0
        self._t_last = time.perf_counter()
        self._aim_y_offset = 0.2

    @classmethod
    def from_config(cls, path="config.yaml", live=True, weights=None):
        cfg = yaml.safe_load(open(path))
        v = cfg["vision"]
        masks = [tuple(m) for m in v.get("masks") or []]
        weights = weights or v["weights"]
        det = Detector(weights, v.get("imgsz", 640), v.get("classes"), masks, v.get("min_box_h_frac", 0),
                       max_aspect=v.get("max_aspect", 1.3))
        cap = None
        if live:
            c = cfg["capture"]
            region = tuple(c["region"]) if c.get("region") else None
            if region is None and c.get("window_title"):
                region = find_window_region(c["window_title"])
                if region is None:
                    raise RuntimeError(f"No window titled like '{c['window_title']}'")
            cap = Capture(region, c.get("target_fps", 60))
        backend = "TensorRT" if str(weights).endswith(".engine") else ("FP16" if det.quantize else "FP32")
        hud = HUD(subtitle=f"ON-DEVICE // {os.path.basename(str(weights)).split('.')[0]} // {backend}")
        return cls(det, cap, hud, masks)

    @property
    def device_name(self):
        if not self.det.device.startswith("cuda"):
            return "CPU"
        import torch
        return torch.cuda.get_device_name(0).replace("NVIDIA GeForce ", "")

    def step(self, conf=0.3, rule="nearest", aim_y_offset=0.2, frame=None, ego=(0.0, 0.0)):
        """Grab (or take) a frame, detect, track, select target. Returns an Observation."""
        if frame is None:
            frame = self.cap.grab()
        H, W = frame.shape[:2]
        t0 = time.perf_counter()
        boxes = self.det.detect(frame, conf)
        infer_ms = (time.perf_counter() - t0) * 1000
        tracks = self.tracker.update(boxes, W, ego)
        target = self.tracker.select(rule, (W / 2, H / 2))
        now = time.perf_counter()
        self.fps = 0.9 * self.fps + 0.1 / max(now - self._t_last, 1e-6)
        self._t_last = now
        self._aim_y_offset = aim_y_offset

        obs = Observation(now, frame, tracks, target, self.tracker.lock_frames, infer_ms=infer_ms)
        if target is not None:
            ax, ay = target.box.aim_point(aim_y_offset)
            obs.err_x, obs.err_y = ax - W / 2, ay - H / 2
            obs.target_h_frac = target.box.h / H
        obs.nearest_h_frac = max((t.box.h / H for t in tracks), default=0.0)
        obs.telemetry = {
            "n_boxes": len(boxes), "n_tracks": len(tracks),
            "target_id": target.id if target else None,
            "err_x": round(obs.err_x, 1), "err_y": round(obs.err_y, 1),
            "target_h_frac": round(obs.target_h_frac, 3), "nearest_h_frac": round(obs.nearest_h_frac, 3),
            "lock_frames": obs.lock_frames, "infer_ms": round(infer_ms, 2),
        }
        self.last = obs
        return obs

    def hud_frame(self, info=None, fire_radius_px=None, retreat_prox=0.45, show_masks=False, radar=None):
        o = self.last
        return self.hud.render(o.frame, o.tracks, o.target, o.lock_frames,
                               stats={"fps": self.fps, "infer_ms": o.infer_ms, "device": self.device_name},
                               info=info, aim_y_offset=self._aim_y_offset, fire_radius_px=fire_radius_px,
                               retreat_prox=retreat_prox, masks=self.masks if show_masks else (), radar=radar)

    def close(self):
        if self.cap:
            self.cap.close()
