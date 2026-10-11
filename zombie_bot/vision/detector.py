"""On-device zombie detector (YOLO11n). Returns boxes in full-frame pixel coords."""
from dataclasses import dataclass
import math

import cv2
import numpy as np


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float

    @property
    def w(self):
        return self.x2 - self.x1

    @property
    def h(self):
        return self.y2 - self.y1

    @property
    def cx(self):
        return (self.x1 + self.x2) / 2

    @property
    def cy(self):
        return (self.y1 + self.y2) / 2

    def aim_point(self, y_offset):
        """y_offset 0 = top of box (head), 0.5 = center."""
        return self.cx, self.y1 + y_offset * self.h


class Detector:
    def __init__(self, weights="yolo11n.pt", imgsz=640, classes=(0,), masks=(), min_box_h_frac=0.0, device=None,
                 max_aspect=1.3):
        """masks: list of (x1, y1, x2, y2) in 0..1 frame fractions, painted black before
        inference (own gun/hands, HUD). classes: (0,) = COCO person; None for a fine-tuned model.
        max_aspect: drop boxes wider than max_aspect x their height (corpses lying on the floor)."""
        import torch
        from ultralytics import YOLO

        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model = YOLO(weights, task="detect")
        # TensorRT/ONNX engines have precision baked in; only .pt needs the fp16 flag
        self.quantize = 16 if self.device.startswith("cuda") and str(weights).endswith(".pt") else None
        self.imgsz = imgsz
        self.classes = list(classes) if classes is not None else None
        self.masks = list(masks)
        self.min_box_h_frac = min_box_h_frac
        self.max_aspect = max_aspect
        if self.device == "cpu":
            print("[detector] WARNING: running on CPU, expect <10 fps")
        # warmup so the first real frame isn't slow
        self.model.predict(np.zeros((imgsz, imgsz, 3), np.uint8), imgsz=imgsz, device=self.device,
                           quantize=self.quantize, verbose=False)

    def apply_masks(self, frame):
        if not self.masks:
            return frame
        frame = frame.copy()
        H, W = frame.shape[:2]
        for x1, y1, x2, y2 in self.masks:
            frame[int(y1 * H):int(y2 * H), int(x1 * W):int(x2 * W)] = 0
        return frame

    def detect(self, frame, conf=0.25):
        H = frame.shape[0]
        res = self.model.predict(self.apply_masks(frame), imgsz=self.imgsz, conf=conf, classes=self.classes,
                                 device=self.device, quantize=self.quantize, verbose=False)[0]
        out = []
        for (x1, y1, x2, y2), c in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist()):
            if (y2 - y1) >= self.min_box_h_frac * H and (x2 - x1) <= self.max_aspect * (y2 - y1):
                out.append(Box(x1, y1, x2, y2, c))
        return out


def pick_target(boxes, rule, crosshair):
    """rule: nearest (tallest box = closest zombie), largest (area), center (closest to crosshair)."""
    if not boxes:
        return None
    if rule == "nearest":
        return max(boxes, key=lambda b: b.h)
    if rule == "largest":
        return max(boxes, key=lambda b: b.w * b.h)
    cx, cy = crosshair
    return min(boxes, key=lambda b: math.hypot(b.cx - cx, b.cy - cy))


def draw(frame, boxes, target=None, masks=(), fps=None, aim_y_offset=0.2):
    out = frame.copy()
    H, W = out.shape[:2]
    for x1, y1, x2, y2 in masks:
        cv2.rectangle(out, (int(x1 * W), int(y1 * H)), (int(x2 * W), int(y2 * H)), (80, 80, 80), 1)
    for b in boxes:
        color = (0, 0, 255) if b is target else (0, 255, 0)
        cv2.rectangle(out, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), color, 2)
        cv2.putText(out, f"{b.conf:.2f} h{int(b.h)}", (int(b.x1), int(b.y1) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cx, cy = W // 2, H // 2
    cv2.drawMarker(out, (cx, cy), (255, 255, 255), cv2.MARKER_CROSS, 16, 1)
    if target is not None:
        ax, ay = target.aim_point(aim_y_offset)
        cv2.line(out, (cx, cy), (int(ax), int(ay)), (0, 0, 255), 1)
    if fps is not None:
        cv2.putText(out, f"{fps:.1f} fps  {len(boxes)} det", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    return out
