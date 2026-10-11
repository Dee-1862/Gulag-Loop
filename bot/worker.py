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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["calibrate", "run"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--map", default="kino")
    ap.add_argument("--jobs", default=None)
    ap.add_argument("--baseline", type=int, default=0, help="run the baseline strategy N times (no jobs file)")
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--max-s", type=float, default=180)
    ap.add_argument("--out-dir", default=data_path("episodes", ""), help="HUD recordings per episode")
    ap.add_argument("--save-frames", default=data_path("port_frames", ""), help="raw frames for detector retraining ('' = off)")
    ap.add_argument("--worker", default=socket.gethostname())
    ap.add_argument("--frame-every", type=float, default=1.0, help="seconds between saved raw frames")
    ap.add_argument("--weights", default=None, help="detector weights; default: config.yaml, else model/zombies_v1.*")
    ap.add_argument("--experiment", default=None, help="claim jobs from this MongoDB experiment instead of --jobs")
    ap.add_argument("--propose", action="store_true",
                    help="with --experiment: when the queue is empty, ask Luminal for the next generation")
    ap.add_argument("--generations", type=int, default=8, help="with --experiment: stop after this generation")
    ap.add_argument("--wait-s", type=float, default=10, help="with --experiment: poll interval while waiting")
    args = ap.parse_args()

    from vision.pipeline import VisionPipeline
    from bot.hudread import HudReader

    game = PortGame(args.map, log=log).open()
    log("gpu:", game.gpu())
    try:
        game.start()
        game.align()
        if args.command == "calibrate":
            cpp = calibrate(game)
            json.dump({**load_calib(), "counts_per_px": round(cpp, 4), "machine": args.worker,
                       "calibrated_at": time.strftime("%Y-%m-%d %H:%M")}, open(CALIB, "w"), indent=2)
            log(f"counts_per_px = {cpp:.4f} saved to {CALIB}")
            return

        orch = None
        if args.experiment:
            from mongo_orchestrator import MongoOrchestrator
            orch = MongoOrchestrator(args.experiment)
            exp = orch.db["experiments"].find_one({"_id": args.experiment})
            if exp is None:
                raise SystemExit(f"Unknown experiment {args.experiment}; run `python mongo_orchestrator.py "
                                 f"--experiment {args.experiment} init --map {args.map}` first")
            if exp["map"] != args.map:
                raise SystemExit(f"Experiment {args.experiment} is on map {exp['map']!r}, not {args.map!r}")
            jobs = []
        elif args.jobs:
            jobs = json.load(open(args.jobs))
        else:
            jobs = [{"job_id": f"baseline-local-{i}-{int(time.time())}", "strategy_id": "baseline",
                     "kind": "baseline", "params": BASELINE} for i in range(args.baseline)]
        results = [] if orch else (json.load(open(args.results)) if os.path.exists(args.results) else [])
        done = {r["job_id"] for r in results}
        cpp = load_calib().get("counts_per_px")
        if cpp is None:
            cpp = 0.15
            log("WARNING: no calibration.json; using counts_per_px=0.15. Run `python -m bot.worker calibrate`.")
        from vision.capture import Capture
        cap = Capture(game.canvas_region(), 60)  # one capture per worker: dxcam allows one per output
        log(f"capture backend: {cap.backend} region {game.canvas_region()}")
        import yaml
        weights = resolve_weights(args.weights or yaml.safe_load(open(args.config))["vision"]["weights"])
        log(f"detector weights: {weights}")
        vp = VisionPipeline.from_config(args.config, live=False, weights=weights)
        hud = HudReader()
        os.makedirs(args.out_dir, exist_ok=True)
        log(f"{'MongoDB experiment ' + args.experiment if orch else str(len(jobs)) + ' jobs'}, counts_per_px={cpp}")

        luminal = []  # created on first proposal, then reused across generations

        def job_stream():
            """Jobs from the file, or claimed one at a time from MongoDB until the last generation is done."""
            if orch is None:
                yield from (j for j in jobs if j["job_id"] not in done)
                return
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

        fresh = True  # the session opened above is a new game
        for i, job in enumerate(job_stream()):
            if not fresh:
                game.start()  # reload = fresh game; assets come from cache
                old = game.canvas_region()
                if game.align() != old:  # window moved: rebuild the capture
                    cap.close()
                    cap = Capture(game.canvas_region(), 60)
            fresh = False
            vp.tracker.__init__()  # fresh track ids per episode
            hud.points_hist.clear()
            hud.points = None
            log(f"job {i + 1}/{len(jobs) if orch is None else '?'} {job['kind']} {job['strategy_id'][:8]}")
            res = run_episode(game, vp, hud, job["params"], max_s=args.max_s, counts_per_px=cpp,
                              record_path=os.path.join(args.out_dir, f"{job['job_id']}.mp4"),
                              save_frames_dir=os.path.join(args.save_frames, args.map) if args.save_frames else None,
                              log=log, cap=cap, frame_every_s=args.frame_every)
            log(f"  -> points +{res['points']} survival {res['survival_s']}s game_over={res['game_over']} "
                f"diag={json.dumps(res['diagnostics'])[:300]}")
            if res["aborted"]:
                log("aborted by F8; not recording this job")
                if orch:
                    orch.release_job(job["job_id"], args.worker)  # back in the queue for the next run
                break
            result = {"job_id": job["job_id"], "strategy_id": job["strategy_id"], "worker": args.worker,
                      "map": args.map, "points": res["points"], "survival_s": res["survival_s"],
                      "diagnostics": res["diagnostics"]}
            if orch:
                orch.complete_job(result)
                log("  stored in MongoDB")
                continue
            results.append(result)
            with open(args.results, "w") as f:  # write after every job so a crash loses nothing
                json.dump(results, f, indent=2)
    finally:
        game.close()


if __name__ == "__main__":
    main()
