"""Game worker: runs evaluation jobs on the browser port and writes results for the orchestrator.

Contract (TEAMMATE_HANDOFF.md on origin/jeffrey):
  jobs:    output of `evolve_local.py --state S jobs`  -> [{job_id, strategy_id, kind, params, ...}]
  results: input of `evolve_local.py --state S ingest --results R`
           -> [{job_id, strategy_id, worker, map, points, survival_s, diagnostics}]

python -m bot.worker calibrate                         # once per machine: mouse counts per screen px
python -m bot.worker run --jobs jobs.json --results results.json
python -m bot.worker run --baseline 2 --results baseline.json   # no orchestrator needed
python -m bot.worker run --experiment exp-kino-1 --propose --generations 8   # MongoDB queue + Luminal
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
from common.paths import data_path, resolve_weights


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
            cv2.imwrite(data_path("port", "calib_a.png"), a.astype(np.uint8))
            cv2.imwrite(data_path("port", "calib_b.png"), b.astype(np.uint8))
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

    def __init__(self, map_id="kino", config="config.yaml", out_dir=None,
                 save_frames=None, frame_every=1.0, max_s=180.0, worker=None,
                 weights=None, db=None, experiment_id=None, live_view=False):
        import yaml
        from vision.capture import Capture
        from vision.pipeline import VisionPipeline
        from bot.hudread import HudReader
        out_dir = out_dir or data_path("episodes", "")
        save_frames = data_path("port_frames", "") if save_frames is None else save_frames
        self.db, self.experiment_id = db, experiment_id  # entry points are learned per map in MongoDB
        self.live_view = live_view
        if live_view:  # create the window BEFORE the game opens, so the game ends up with focus
            cv2.namedWindow("ZOMBIE VISION (live)", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("ZOMBIE VISION (live)", 960, 540)
            cv2.imshow("ZOMBIE VISION (live)", np.zeros((540, 960, 3), np.uint8))
            cv2.waitKey(1)
        # Load the detector and OCR BEFORE starting the game: they take ~1 min, and an idle player
        # in round 1 gets killed before the first episode begins.
        weights = resolve_weights(weights or yaml.safe_load(open(config))["vision"]["weights"])
        log(f"detector weights: {weights}")
        self.vp = VisionPipeline.from_config(config, live=False, weights=weights)
        self.hud = HudReader()
        log("detector and HUD reader loaded")
        self.map_id, self.out_dir, self.max_s = map_id, out_dir, max_s
        self.save_frames, self.frame_every = save_frames, frame_every
        self.worker = worker or socket.gethostname()
        self.cpp = load_calib().get("counts_per_px")
        if self.cpp is None:
            self.cpp = 0.15
            log("WARNING: no calibration.json; using counts_per_px=0.15. Run `python -m bot.worker calibrate`.")
        os.makedirs(out_dir, exist_ok=True)
        self.game = PortGame(map_id, log=log).open()
        log("gpu:", self.game.gpu())
        self.game.start()
        # Geometric viewport estimate on purpose: template alignment succeeds only sometimes, and a
        # session-dependent 25 px offset would make episodes incomparable and move the HUD OCR crops.
        self._Capture = Capture
        self.cap = Capture(self.game.canvas_region(), 60)  # one capture per worker: dxcam allows one per output
        log(f"capture backend: {self.cap.backend} region {self.game.canvas_region()}, counts_per_px={self.cpp}")
        self.fresh = True

    def run(self, job):
        """Play one episode for a job. Returns a result dict in the ingest format, or None if aborted (F8)."""
        if not self.fresh:
            self.game.start()  # reload = fresh game; assets come from cache
        self.fresh = False
        self.vp.tracker.__init__()  # fresh track ids per episode
        self.hud.points_hist.clear()
        self.hud.points = None
        res = run_episode(self.game, self.vp, self.hud, job["params"], max_s=self.max_s, counts_per_px=self.cpp,
                          record_path=os.path.join(self.out_dir, f"{job['job_id']}.mp4"),
                          save_frames_dir=os.path.join(self.save_frames, self.map_id) if self.save_frames else None,
                          log=log, cap=self.cap, frame_every_s=self.frame_every,
                          map_name=self.map_id, db=self.db, experiment_id=self.experiment_id, job_id=job["job_id"],
                          live_view=self.live_view)
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
    ap.add_argument("--out-dir", default=None, help="HUD recordings per episode (default: <data>/episodes)")
    ap.add_argument("--save-frames", default=None,
                    help="raw frames for detector retraining (default: <data>/port_frames, '' = off)")
    ap.add_argument("--worker", default=socket.gethostname())
    ap.add_argument("--frame-every", type=float, default=1.0, help="seconds between saved raw frames")
    ap.add_argument("--weights", default=None, help="detector weights; default: config.yaml, else model/zombies_v1.*")
    ap.add_argument("--live-view", action="store_true",
                    help="show the HUD (red overlays + 360 radar) live in a window; put it beside the game")
    ap.add_argument("--experiment", default=None, help="claim jobs from this MongoDB experiment instead of --jobs")
    ap.add_argument("--propose", action="store_true",
                    help="with --experiment: when the queue is empty, ask Luminal for the next generation")
    ap.add_argument("--generations", type=int, default=8, help="with --experiment: stop after this generation")
    ap.add_argument("--wait-s", type=float, default=10, help="with --experiment: poll interval while waiting")
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

    if args.experiment:
        run_mongo(args)
        return
    if args.jobs:
        jobs = json.load(open(args.jobs))
    else:
        jobs = [{"job_id": f"baseline-local-{i}-{int(time.time())}", "strategy_id": "baseline",
                 "kind": "baseline", "params": BASELINE} for i in range(args.baseline)]
    os.makedirs(os.path.dirname(os.path.abspath(args.results)), exist_ok=True)
    results = json.load(open(args.results)) if os.path.exists(args.results) else []
    done = {r["job_id"] for r in results}
    try:  # entry points are learned per map in MongoDB, also when jobs come from a file
        from common.db import get_db
        db = get_db()
    except Exception as e:
        db = None
        log(f"MongoDB not available for entry points ({type(e).__name__})")
    runner = Runner(args.map, args.config, args.out_dir, args.save_frames, args.frame_every, args.max_s, args.worker,
                    weights=args.weights, db=db, live_view=args.live_view)
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


def run_mongo(args):
    """Claim jobs from a MongoDB experiment, store each run, and (with --propose) ask Luminal for the next
    generation when the queue is empty. Other laptops run the same command without --propose."""
    from mongo_orchestrator import MongoOrchestrator
    orch = MongoOrchestrator(args.experiment)
    exp = orch.db["experiments"].find_one({"_id": args.experiment})
    if exp is None:
        raise SystemExit(f"Unknown experiment {args.experiment}; run `python mongo_orchestrator.py "
                         f"--experiment {args.experiment} init --map {args.map}` first")
    if exp["map"] != args.map:
        raise SystemExit(f"Experiment {args.experiment} is on map {exp['map']!r}, not {args.map!r}")
    luminal = []  # created on first proposal, then reused across generations

    def job_stream():
        while True:
            job = orch.claim_job(args.worker)
            if job:
                yield job
                continue
            st = orch.status()
            busy = st["jobs"].get("queued", 0) + st["jobs"].get("running", 0)
            if busy == 0 and st["generation"] >= args.generations:
                log(f"generation {st['generation']} complete; stopping")
                return
            if busy == 0 and args.propose:
                if not luminal:
                    from luminal_client import LuminalClient
                    luminal.append(LuminalClient())
                    orch.rehydrate_cache(luminal[0])
                log(f"queue empty: asking Luminal for generation {st['generation'] + 1}")
                out = orch.propose(luminal[0])
                log(f"  generation {out['generation']}: {len(out['children'])} children "
                    f"({sum(c['fallback'] for c in out['children'])} fallbacks), {len(out['jobs'])} jobs queued")
                continue
            time.sleep(args.wait_s)  # other workers are still running, or another machine proposes

    runner = Runner(args.map, args.config, args.out_dir, args.save_frames, args.frame_every, args.max_s, args.worker,
                    weights=args.weights, db=orch.db, experiment_id=args.experiment, live_view=args.live_view)
    try:
        for i, job in enumerate(job_stream()):
            log(f"job {i + 1} {job['kind']} {job['strategy_id'][:8]} (MongoDB {args.experiment})")
            r = runner.run(job)
            if r is None:
                orch.release_job(job["job_id"], args.worker)  # F8: back in the queue for the next run
                break
            orch.complete_job(r)
            log("  stored in MongoDB")
    finally:
        runner.close()


if __name__ == "__main__":
    main()
