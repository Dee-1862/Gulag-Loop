"""Game worker: runs evaluation jobs on the browser port and writes results for the orchestrator.

Contract (TEAMMATE_HANDOFF.md on origin/jeffrey):
  jobs:    output of `evolve_local.py --state S jobs`  -> [{job_id, strategy_id, kind, params, ...}]
  results: input of `evolve_local.py --state S ingest --results R`
           -> [{job_id, strategy_id, worker, map, points, survival_s, diagnostics}]

python -m bot.worker calibrate                         # once per machine: mouse counts per screen px
python -m bot.worker run --jobs jobs.json --results results.json
python -m bot.worker run --baseline 2 --results baseline.json   # no orchestrator needed
The mouse belongs to the game while this runs. F8 aborts the current episode; Esc in game releases the mouse.
"""
import argparse
import json
import os
import socket
import statistics
import time

import cv2
import numpy as np
from bot.controller import BASELINE, run_episode
from bot.game import PortGame


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def calibrate(game, counts=20, reps=6):
    """Screen px of camera shift per mouse count, from phase correlation of small turns (no zombies yet)."""
    import pydirectinput
    from vision.capture import Capture
    pydirectinput.FAILSAFE = False
    cap = Capture(game.canvas_region(), 60)

    def grab():
        time.sleep(0.25)
        return cv2.cvtColor(cv2.resize(cap.grab(), (1280, 720)), cv2.COLOR_BGR2GRAY)[180:540, 320:960].astype(np.float32)

    time.sleep(6)  # input is ignored during the spawn intro ("Round" banner)
    log(f"calibrating: pointer locked={game.locked()}")
    vals = []
    for r in range(reps):
        sgn = 1 if r % 2 == 0 else -1
        a = grab()
        pydirectinput.moveRel(sgn * counts, 0, relative=True, disable_mouse_acceleration=True)
        b = grab()
        if r == 0:
            cv2.imwrite("D:/czb/port/calib_a.png", a.astype(np.uint8))
            cv2.imwrite("D:/czb/port/calib_b.png", b.astype(np.uint8))
        (dx, dy), resp = cv2.phaseCorrelate(a, b)
        log(f"calib move {sgn * counts:+d} counts -> dx {dx:+.1f}px dy {dy:+.1f}px (resp {resp:.2f})")
        if resp > 0.1 and abs(dx) > 1:
            vals.append(abs(dx) / counts)
    cap.close()
    if not vals:
        raise RuntimeError("calibration failed: camera did not visibly turn (pointer lock? paused?)")
    px_per_count = statistics.median(vals)
    return 1.0 / px_per_count


CALIB = "calibration.json"  # per machine (sensitivity, DPI), gitignored


def load_calib():
    return json.load(open(CALIB)) if os.path.exists(CALIB) else {}


class Runner:
    """One open game session that evaluates jobs back to back (used by worker `run` and bot.loop)."""

    def __init__(self, map_id="kino", config="config.yaml", out_dir="D:/czb/episodes",
                 save_frames="D:/czb/port_frames", frame_every=1.0, max_s=180.0, worker=None,
                 slot=None, input_mode="os", ocr_gpu=True, latency_s=0.11):
        from vision.capture import Capture
        from vision.pipeline import VisionPipeline
        from bot.hudread import HudReader
        self.map_id, self.out_dir, self.max_s = map_id, out_dir, max_s
        self.save_frames, self.frame_every = save_frames, frame_every
        self.worker = worker or socket.gethostname()
        self.slot, self.latency_s = slot, latency_s
        self.cpp = load_calib().get("counts_per_px")
        if self.cpp is None:
            self.cpp = 0.15
            log("WARNING: no calibration.json; using counts_per_px=0.15. Run `python -m bot.worker calibrate`.")
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        self.game = PortGame(map_id, log=log, slot=slot, input_mode=input_mode).open()
        log("gpu:", self.game.gpu())
        self.game.start()
        # Geometric viewport estimate on purpose: template alignment succeeds only sometimes, and a
        # session-dependent 25 px offset would make episodes incomparable and move the HUD OCR crops.
        self._Capture = Capture
        self.cap = self._new_capture()
        log(f"capture backend: {self.cap.backend} region {self.game.canvas_region()}, counts_per_px={self.cpp}")
        self.vp = VisionPipeline.from_config(config, live=False)
        self.hud = HudReader(gpu=ocr_gpu)
        self.fresh = True

    def _new_capture(self):
        if self.slot is not None:  # tiles can be covered by other windows: read the page's own frames
            from bot.screencast import ScreencastCapture
            return ScreencastCapture(self.game.page)
        return self._Capture(self.game.canvas_region(), 60)  # one capture per worker: dxcam allows one per output

    def run(self, job):
        """Play one episode for a job. Returns a result dict in the ingest format, or None if aborted (F8)."""
        if not self.fresh:
            try:
                self.game.start()  # reload = fresh game; assets come from cache
            except Exception as e:  # browser/page gone (crash or closed window): reopen and retry once
                log(f"game restart failed ({type(e).__name__}); reopening the browser")
                try:
                    self.game.close()
                except Exception:
                    pass
                self.game = PortGame(self.map_id, log=log, slot=self.slot, input_mode=self.game.input_mode).open()
                self.game.start()
                self.cap.close()
                self.cap = self._new_capture()
            if self.slot is not None:
                self.cap.close()
                self.cap = self._new_capture()
        self.fresh = False
        self.vp.tracker.__init__()  # fresh track ids per episode
        self.hud.points_hist.clear()
        self.hud.points = None
        res = run_episode(self.game, self.vp, self.hud, job["params"], max_s=self.max_s, counts_per_px=self.cpp,
                          record_path=os.path.join(self.out_dir, f"{job['job_id']}.mp4") if self.out_dir else None,
                          save_frames_dir=os.path.join(self.save_frames, self.map_id) if self.save_frames else None,
                          log=log, cap=self.cap, frame_every_s=self.frame_every, latency_s=self.latency_s)
        log(f"  -> points +{res['points']} survival {res['survival_s']}s game_over={res['game_over']} "
            f"diag={json.dumps(res['diagnostics'])[:300]}")
        if res["aborted"]:
            log("aborted by F8; not recording this job")
            return None
        return {"job_id": job["job_id"], "strategy_id": job["strategy_id"], "worker": self.worker,
                "map": self.map_id, "points": res["points"], "survival_s": res["survival_s"],
                "diagnostics": res["diagnostics"]}

    def close(self):
        try:
            self.cap.close()
        finally:
            self.game.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["calibrate", "run"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--map", default="kino")
    ap.add_argument("--jobs", default=None)
    ap.add_argument("--baseline", type=int, default=0, help="run the baseline strategy N times (no jobs file)")
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--max-s", type=float, default=180)
    ap.add_argument("--out-dir", default="D:/czb/episodes", help="HUD recordings per episode")
    ap.add_argument("--save-frames", default="D:/czb/port_frames", help="raw frames for detector retraining ('' = off)")
    ap.add_argument("--worker", default=socket.gethostname())
    ap.add_argument("--frame-every", type=float, default=1.0, help="seconds between saved raw frames")
    args = ap.parse_args()

    if args.command == "calibrate":
        game = PortGame(args.map, log=log).open()
        try:
            game.start()
            game.align()
            cpp = calibrate(game)
            json.dump({**load_calib(), "counts_per_px": round(cpp, 4), "machine": args.worker,
                       "calibrated_at": time.strftime("%Y-%m-%d %H:%M")}, open(CALIB, "w"), indent=2)
            log(f"counts_per_px = {cpp:.4f} saved to {CALIB}")
        finally:
            game.close()
        return

    if args.jobs:
        jobs = json.load(open(args.jobs))
    else:
        jobs = [{"job_id": f"baseline-local-{i}-{int(time.time())}", "strategy_id": "baseline",
                 "kind": "baseline", "params": BASELINE} for i in range(args.baseline)]
    os.makedirs(os.path.dirname(os.path.abspath(args.results)), exist_ok=True)
    results = json.load(open(args.results)) if os.path.exists(args.results) else []
    done = {r["job_id"] for r in results}
    runner = Runner(args.map, args.config, args.out_dir, args.save_frames, args.frame_every, args.max_s, args.worker)
    try:
        for i, job in enumerate(jobs):
            if job["job_id"] in done:
                continue
            log(f"job {i + 1}/{len(jobs)} {job['kind']} {job['strategy_id'][:8]}")
            r = runner.run(job)
            if r is None:
                break
            results.append(r)
            with open(args.results, "w") as f:  # write after every job so a crash loses nothing
                json.dump(results, f, indent=2)
    finally:
        runner.close()


if __name__ == "__main__":
    main()
