"""Before/after reel: two detectors on the same held-out footage, side by side, with the HUD.

python -m vision.compare --source D:/czb/footage/kino_solo_mid.mp4 --a D:/czb/models/yolo11n.engine \
    --b D:/czb/models/zombies_v1.engine --record D:/czb/renders/before_after.mp4 --start 20 --max-seconds 30
"""
import argparse

import cv2
import numpy as np
import yaml

from vision.hud import DARK, HUD, TextCache, WHITE, ACCENT, DANGER
from vision.pipeline import VisionPipeline
from vision.run import Recorder


def caption(img, text, sub, color):
    tc = TextCache()
    H, W = img.shape[:2]
    cv2.rectangle(img, (0, H - 64), (W, H), DARK, -1)
    cv2.rectangle(img, (0, H - 64), (6, H), color, -1)
    tc.put(img, text, (20, H - 56), 26, color)
    tc.put(img, sub, (20, H - 24), 15, WHITE, mono=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--a", default="D:/czb/models/yolo11n.engine")
    ap.add_argument("--a-classes", default="0", help="comma list, or 'none'")
    ap.add_argument("--a-label", default="BEFORE: off-the-shelf COCO 'person'")
    ap.add_argument("--b", required=True)
    ap.add_argument("--b-label", default="AFTER: distilled zombie detector")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--record", required=True)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--max-seconds", type=float, default=30)
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--conf", type=float, default=0.3)
    args = ap.parse_args()

    va = VisionPipeline.from_config(args.config, live=False, weights=args.a)
    va.det.classes = None if args.a_classes == "none" else [int(c) for c in args.a_classes.split(",")]
    vb = VisionPipeline.from_config(args.config, live=False, weights=args.b)
    vb.det.classes = None

    vc = cv2.VideoCapture(args.source)
    src_fps = vc.get(cv2.CAP_PROP_FPS) or 60
    vc.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    stride = max(1, int(round(src_fps / args.fps)))
    rec = None
    n = hits_a = hits_b = 0
    i = 0
    while i < args.max_seconds * src_fps:
        ok, frame = vc.read()
        if not ok:
            break
        i += 1
        if i % stride:
            continue
        frame = cv2.resize(frame, (1280, 720))
        oa = va.step(args.conf, frame=frame)
        ob = vb.step(args.conf, frame=frame)
        n += 1
        hits_a += bool(oa.tracks)
        hits_b += bool(ob.tracks)
        left = va.hud_frame(info={"action": "SCAN" if oa.target is None else "ENGAGE"})
        right = vb.hud_frame(info={"action": "SCAN" if ob.target is None else "ENGAGE"})
        caption(left, args.a_label, f"frames with a zombie locked: {hits_a / n:5.0%}", DANGER)
        caption(right, args.b_label, f"frames with a zombie locked: {hits_b / n:5.0%}", ACCENT)
        out = cv2.hconcat([left, np.zeros((720, 8, 3), np.uint8), right])
        if rec is None:
            rec = Recorder(args.record, out.shape[1], out.shape[0], args.fps)
        rec.write(out)
    rec.close()
    print(f"[compare] frames={n}  A tracked {hits_a / n:.0%}  B tracked {hits_b / n:.0%}  -> {args.record}")


if __name__ == "__main__":
    main()
