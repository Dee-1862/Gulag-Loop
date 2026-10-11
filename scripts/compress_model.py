"""Compress the zombie detector and measure what it costs: size, speed (GPU and CPU), agreement with the original.

    python scripts/compress_model.py            # builds model/zombies_v1_int8.onnx (+ TensorRT INT8 if it can)
                                                # and writes results/compression.md

Variants: PyTorch FP32 (reference), ONNX FP32, ONNX INT8 (static QDQ, calibrated on real port frames),
TensorRT FP16, TensorRT INT8. Every variant runs through vision.detector.Detector, i.e. the same masks and
corpse filter as the bot. There are no hand labels, so "agreement" = how many of the reference model's boxes
the variant reproduces (IoU >= 0.5) and how many of its boxes the reference also found; it measures
compression loss, not absolute accuracy.
"""
import glob
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402

from vision.detector import Detector  # noqa: E402

CFG = yaml.safe_load(open("config.yaml"))["vision"]
MASKS = [tuple(m) for m in CFG["masks"]]
FRAMES = sorted(glob.glob("runtime/port_frames/kino/*.jpg")) or sorted(glob.glob("local_data/port_frames/kino/*.jpg"))
random.Random(0).shuffle(FRAMES)
CALIB, EVAL = FRAMES[:300], FRAMES[300:500] or FRAMES[:100]
CONF = 0.25


def letterbox(img, size=640):
    h, w = img.shape[:2]
    s = size / max(h, w)
    nh, nw = int(round(h * s)), int(round(w * s))
    out = np.full((size, size, 3), 114, np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    out[top:top + nh, left:left + nw] = cv2.resize(img, (nw, nh))
    return out


def build_onnx_int8(src="model/zombies_v1.onnx", dst="model/zombies_v1_int8.onnx"):
    from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_static
    masker = Detector.__new__(Detector)
    masker.masks = MASKS

    class Reader(CalibrationDataReader):
        def __init__(self):
            import onnxruntime as ort
            self.name = ort.InferenceSession(src, providers=["CPUExecutionProvider"]).get_inputs()[0].name
            self.it = iter(CALIB[:24])  # min/max ranges; more frames only costs memory

        def get_next(self):
            p = next(self.it, None)
            if p is None:
                return None
            img = masker.apply_masks(cv2.resize(cv2.imread(p), (1280, 720)))
            x = letterbox(img)[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
            return {self.name: np.ascontiguousarray(x)}

    quantize_static(src, dst, Reader(), quant_format=QuantFormat.QDQ, per_channel=True,
                    activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8)
    return dst


def build_trt_int8(dst="model/zombies_v1_int8.engine"):
    from ultralytics import YOLO
    calib_dir = ROOT / "local_data" / "calib" / "images"
    calib_dir.mkdir(parents=True, exist_ok=True)
    for p in CALIB:
        out = calib_dir / Path(p).name
        if not out.exists():
            masker = Detector.__new__(Detector)
            masker.masks = MASKS
            cv2.imwrite(str(out), masker.apply_masks(cv2.resize(cv2.imread(p), (1280, 720))))
    data = ROOT / "local_data" / "calib" / "calib.yaml"
    data.write_text(f"path: {calib_dir.parent.as_posix()}\ntrain: images\nval: images\nnames:\n  0: zombie\n")
    out = YOLO("model/zombies_v1.pt").export(format="engine", int8=True, data=str(data), imgsz=640, workspace=2,
                                             batch=1, verbose=False)
    if out and Path(out).exists() and Path(out) != Path(dst):
        Path(out).replace(dst)
    return dst


def run(weights, device, frames):
    det = Detector(weights, CFG.get("imgsz", 640), CFG.get("classes"), MASKS, CFG.get("min_box_h_frac", 0),
                   device=device, max_aspect=CFG.get("max_aspect", 1.3))
    imgs = [cv2.resize(cv2.imread(p), (1280, 720)) for p in frames]
    for im in imgs[:5]:  # warm-up
        det.detect(im, CONF)
    t0 = time.perf_counter()
    out = [[(b.x1, b.y1, b.x2, b.y2) for b in det.detect(im, CONF)] for im in imgs]
    return out, (time.perf_counter() - t0) / len(imgs) * 1000


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0


def agreement(ref, test):
    found = sum(1 for r, t in zip(ref, test) for box in r if any(iou(box, x) >= 0.5 for x in t))
    kept = sum(1 for r, t in zip(ref, test) for box in t if any(iou(box, x) >= 0.5 for x in r))
    n_ref, n_test = sum(map(len, ref)), sum(map(len, test))
    return (found / n_ref if n_ref else 1.0), (kept / n_test if n_test else 1.0)


def main():
    print(f"{len(FRAMES)} real port frames: {len(CALIB)} for calibration, {len(EVAL)} for evaluation")
    if not Path("model/zombies_v1_int8.onnx").exists():
        print("building ONNX INT8 ...", flush=True)
        build_onnx_int8()
    if not Path("model/zombies_v1_int8.engine").exists():
        try:
            print("building TensorRT INT8 ...", flush=True)
            build_trt_int8()
        except Exception as e:
            print(f"TensorRT INT8 skipped ({type(e).__name__}: {str(e)[:120]})")
    variants = [("PyTorch FP32 (reference)", "model/zombies_v1.pt", "cuda:0"),
                ("TensorRT FP16", "model/zombies_v1.engine", "cuda:0"),
                ("TensorRT INT8", "model/zombies_v1_int8.engine", "cuda:0"),
                ("PyTorch FP32", "model/zombies_v1.pt", "cpu"),
                ("ONNX FP32", "model/zombies_v1.onnx", "cpu"),
                ("ONNX INT8", "model/zombies_v1_int8.onnx", "cpu")]
    ref, _ = run("model/zombies_v1.pt", "cuda:0", EVAL)
    rows = []
    for name, w, dev in variants:
        if not Path(w).exists():
            continue
        boxes, ms = run(w, dev, EVAL)
        rec, prec = agreement(ref, boxes)
        size = Path(w).stat().st_size / 1e6
        rows.append((name, "GPU" if dev.startswith("cuda") else "CPU", size, ms, rec, prec))
        print(f"{name:<26} {rows[-1][1]}  {size:5.1f} MB  {ms:6.1f} ms/frame  finds {rec:5.1%} of reference boxes, "
              f"{prec:5.1%} of its boxes confirmed", flush=True)
    md = ["# Detector compression", "",
          f"YOLO11n zombie detector (2.6 M parameters). {len(EVAL)} real browser-port frames, conf {CONF}, the bot's "
          "own masks and filters. Agreement is measured against the PyTorch FP32 model (no hand labels): it shows "
          "what compression loses, not absolute accuracy. Times include pre/post-processing; GPU = RTX 4060 Laptop, "
          "CPU = Ryzen 7 8845HS.", "",
          "| Variant | Runs on | Size (MB) | ms / frame | Finds reference boxes | Its boxes confirmed |",
          "| --- | --- | --- | --- | --- | --- |"]
    md += [f"| {n} | {d} | {s:.1f} | {m:.1f} | {r:.0%} | {p:.0%} |" for n, d, s, m, r, p in rows]
    Path("results").mkdir(exist_ok=True)
    Path("results/compression.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("wrote results/compression.md")


if __name__ == "__main__":
    main()
