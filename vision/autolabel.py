"""Teacher -> student distillation, step 1: auto-label gameplay footage.

A big open-vocabulary model (YOLOE-L, prompted with "zombie"/"person") labels frames offline at
~5 fps. The tiny yolo11n student is then trained on those labels and runs at 100+ fps on-device.
Frames are masked (own gun, HUD) exactly like at runtime, so the student never learns the hands.

python -m vision.autolabel --videos D:/czb/footage/*.mp4 --out D:/czb/datasets/zombies --every 0.2
"""
import argparse
import glob
import os
import random

import cv2
import yaml

from vision.detector import Detector

TEACHER_PROMPTS = ["zombie", "person"]


def keep(x1, y1, x2, y2, W, H):
    w, h = x2 - x1, y2 - y1
    if h < 0.03 * H:
        return False  # too tiny to be useful
    if w > 1.3 * h:
        return False  # lying down = corpse, never a target
    return True


def dedupe(boxes, contain=0.75):
    """Drop boxes mostly inside a bigger one (zombie+person double hits, head-in-body boxes)."""
    boxes = sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    out = []
    for b in boxes:
        area = max((b[2] - b[0]) * (b[3] - b[1]), 1e-9)
        if all(max(0, min(b[2], o[2]) - max(b[0], o[0])) * max(0, min(b[3], o[3]) - max(b[1], o[1])) / area < contain
               for o in out):
            out.append(b)
    return out


def dedupe_labels(root):
    """Apply dedupe to an existing YOLO label dir in place."""
    n0 = n1 = 0
    for p in glob.glob(os.path.join(root, "labels", "*", "*.txt")):
        rows = [list(map(float, l.split()[1:])) for l in open(p).read().splitlines() if l.strip()]
        xyxy = [(x - w / 2, y - h / 2, x + w / 2, y + h / 2) for x, y, w, h in rows]
        keep_ = dedupe(xyxy)
        n0 += len(xyxy)
        n1 += len(keep_)
        with open(p, "w") as f:
            f.write("\n".join(f"0 {(a + c) / 2:.6f} {(b + d) / 2:.6f} {c - a:.6f} {d - b:.6f}" for a, b, c, d in keep_))
    print(f"[autolabel] dedupe: {n0} -> {n1} boxes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="+", required=True)
    ap.add_argument("--out", default="D:/czb/datasets/zombies")
    ap.add_argument("--every", type=float, default=0.2, help="seconds between sampled frames")
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--teacher", default="D:/czb/models/yoloe-11l-seg.pt")
    ap.add_argument("--val-frac", type=float, default=0.12)
    ap.add_argument("--neg-keep", type=float, default=0.35, help="fraction of empty frames to keep as negatives")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    from ultralytics import YOLOE
    teacher = YOLOE(args.teacher)
    teacher.set_classes(TEACHER_PROMPTS)
    masks = [tuple(m) for m in yaml.safe_load(open(args.config))["vision"].get("masks") or []]
    masker = Detector.__new__(Detector)  # only need apply_masks, no student model
    masker.masks = masks

    for split in ("train", "val"):
        os.makedirs(f"{args.out}/images/{split}", exist_ok=True)
        os.makedirs(f"{args.out}/labels/{split}", exist_ok=True)

    videos = sorted({p for pat in args.videos for p in glob.glob(pat)})
    random.seed(0)
    total = pos = boxes_n = 0
    for vi, path in enumerate(videos):
        # whole videos go to val, so val frames aren't near-duplicates of train frames
        split = "val" if (vi == len(videos) - 1 and len(videos) > 1) else "train"
        vc = cv2.VideoCapture(path)
        fps = vc.get(cv2.CAP_PROP_FPS) or 30
        step = max(1, int(round(fps * args.every)))
        stem = os.path.splitext(os.path.basename(path))[0]
        i = 0
        while True:
            ok = vc.grab()
            if not ok:
                break
            if i % step == 0:
                ok, frame = vc.retrieve()
                if not ok:
                    break
                frame = cv2.resize(frame, (1280, 720))
                masked = masker.apply_masks(frame)
                H, W = masked.shape[:2]
                r = teacher.predict(masked, conf=args.conf, imgsz=960, device="cuda:0", agnostic_nms=True, verbose=False)[0]
                lines = []
                for x1, y1, x2, y2 in dedupe(r.boxes.xyxy.tolist()):
                    if keep(x1, y1, x2, y2, W, H):
                        lines.append(f"0 {(x1 + x2) / 2 / W:.6f} {(y1 + y2) / 2 / H:.6f} {(x2 - x1) / W:.6f} {(y2 - y1) / H:.6f}")
                if lines or random.random() < args.neg_keep:
                    name = f"{stem}_{i:06d}"
                    cv2.imwrite(f"{args.out}/images/{split}/{name}.jpg", masked, [cv2.IMWRITE_JPEG_QUALITY, 92])
                    with open(f"{args.out}/labels/{split}/{name}.txt", "w") as f:
                        f.write("\n".join(lines))
                    total += 1
                    pos += bool(lines)
                    boxes_n += len(lines)
            i += 1
        print(f"[autolabel] {stem}: done ({split}), running total {total} imgs, {pos} with zombies, {boxes_n} boxes")

    dedupe_labels(args.out)  # idempotent; also cleans labels from older runs
    with open(f"{args.out}/data.yaml", "w") as f:
        yaml.safe_dump({"path": os.path.abspath(args.out), "train": "images/train", "val": "images/val",
                        "names": {0: "zombie"}}, f)
    print(f"[autolabel] wrote {args.out}/data.yaml")


if __name__ == "__main__":
    main()
