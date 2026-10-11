"""On-device vision: capture -> YOLO -> tracker -> HUD. Also the Gate 2 test.

Live game:   python -m vision.run                          (window from config.yaml)
Recorded:    python -m vision.run --source D:/czb/footage/kino_b.mp4 --record out.mp4
Headless:    python -m vision.run --source clip.mp4 --record out.mp4 --no-show --max-seconds 30

Keys (preview window): q quit | s save raw frame | a auto-save 1/s | [ ] conf -/+ | m show masks | p pause
Prints a Gate 2 summary at exit: % frames with a confirmed zombie track, fps, latency.
"""
import argparse
import glob
import os
import subprocess
import time

import cv2
import yaml

from vision.capture import Capture, find_window_region
from vision.pipeline import VisionPipeline


def frame_source(src, cfg):
    if src is None:
        c = cfg["capture"]
        region = tuple(c["region"]) if c.get("region") else None
        if region is None and c.get("window_title"):
            region = find_window_region(c["window_title"])
            if region is None:
                raise SystemExit(f"No window with '{c['window_title']}' in its title. Open the game or set capture.region.")
        print(f"[run] capturing region {region}")
        cap = Capture(region, c.get("target_fps", 60))
        try:
            while True:
                yield cap.grab()
        finally:
            cap.close()
    elif os.path.isdir(src):
        for p in sorted(glob.glob(os.path.join(src, "*.[pj][np]g"))):
            yield cv2.imread(p)
    else:
        vc = cv2.VideoCapture(src)
        while True:
            ok, f = vc.read()
            if not ok:
                break
            yield f


class Recorder:
    """h264 mp4 via ffmpeg pipe (OpenCV's mp4v looks bad and won't play in every editor)."""

    def __init__(self, path, w, h, fps):
        import imageio_ffmpeg
        self.p = subprocess.Popen(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)

    def write(self, frame):
        self.p.stdin.write(frame.tobytes())

    def close(self):
        self.p.stdin.close()
        self.p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--conf", type=float, default=None)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--rule", default="nearest", choices=["nearest", "largest", "center"])
    ap.add_argument("--record", default=None, help="write the HUD stream to this mp4")
    ap.add_argument("--record-fps", type=float, default=30)
    ap.add_argument("--side-by-side", action="store_true", help="record raw | HUD next to each other")
    ap.add_argument("--start", type=float, default=0, help="seconds to skip in a video source")
    ap.add_argument("--max-seconds", type=float, default=None, help="stop after this much source time")
    ap.add_argument("--no-show", action="store_true")
    ap.add_argument("--out", default="dataset/raw")
    ap.add_argument("--display-scale", type=float, default=0.6)
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    vp = VisionPipeline.from_config(args.config, live=False, weights=args.weights)
    conf = args.conf if args.conf is not None else cfg["vision"].get("conf", 0.3)
    print(f"[run] device={vp.det.device} ({vp.device_name})")
    os.makedirs(args.out, exist_ok=True)

    src = frame_source(args.source, cfg)
    is_video = args.source is not None and not os.path.isdir(args.source)
    src_fps = 60.0
    if is_video:
        vc = cv2.VideoCapture(args.source)
        src_fps = vc.get(cv2.CAP_PROP_FPS) or 60.0
        vc.release()
        for _ in range(int(args.start * src_fps)):
            next(src, None)
    # when replaying a 60 fps video into a 30 fps recording, process every other frame
    stride = max(1, int(round(src_fps / args.record_fps))) if (is_video and args.record) else 1

    rec = None
    autosave, show_masks, paused = False, False, False
    n = with_tgt = 0
    infer_ms = 0.0
    last_save = 0.0
    frame = None
    i = 0
    while True:
        if not paused or frame is None:
            frame = None
            for _ in range(stride):
                frame = next(src, None)
            if frame is None:
                break
            i += stride
            if args.max_seconds and is_video and i / src_fps > args.max_seconds:
                break
        if frame.shape[1] != 1280 and is_video:
            frame = cv2.resize(frame, (1280, 720))
        H, W = frame.shape[:2]

        obs = vp.step(conf, args.rule, frame=frame)
        target = obs.target
        now = time.perf_counter()
        n += 1
        with_tgt += target is not None
        infer_ms = 0.8 * infer_ms + 0.2 * obs.infer_ms

        action = "SCAN" if target is None else ("RETREAT" if obs.target_h_frac > 0.45 else "ENGAGE")
        vis = vp.hud_frame(info={"action": action}, show_masks=show_masks)

        if args.record:
            out = cv2.hconcat([frame, vis]) if args.side_by_side else vis
            if rec is None:
                rec = Recorder(args.record, out.shape[1], out.shape[0], args.record_fps)
            rec.write(out)
        if autosave and now - last_save > 1.0:
            cv2.imwrite(os.path.join(args.out, f"{int(time.time() * 1000)}.jpg"), frame)
            last_save = now

        if not args.no_show:
            show = vis if args.display_scale == 1 else cv2.resize(vis, None, fx=args.display_scale, fy=args.display_scale)
            cv2.imshow("zombie-vision", show)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            elif k == ord("s"):
                cv2.imwrite(os.path.join(args.out, f"{int(time.time() * 1000)}.jpg"), frame)
            elif k == ord("a"):
                autosave = not autosave
            elif k == ord("["):
                conf = max(0.05, conf - 0.05)
            elif k == ord("]"):
                conf = min(0.95, conf + 0.05)
            elif k == ord("m"):
                show_masks = not show_masks
            elif k == ord("p"):
                paused = not paused

    if rec:
        rec.close()
    cv2.destroyAllWindows()
    if n:
        print(f"[run] frames={n} with_target={with_tgt / n:.0%} infer={infer_ms:.1f}ms loop_fps~{vp.fps:.1f} "
              f"tracks_created={vp.tracker.next_id - 1}")


if __name__ == "__main__":
    main()
