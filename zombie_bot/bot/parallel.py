"""N games in parallel on one machine: tiled Chrome windows driven by in-page input (no physical mouse).

python -m bot.parallel --workers 4 --state D:/czb/evo_state/kino_par.json --generations 12

Main process: owns the evolution state (Jeffrey's evolution.py), hands one job at a time to each idle
worker, ingests results as they arrive, and asks Luminal for the next generation once every job of the
current generation is done. Workers: one process per game slot, each keeping its game open between jobs.
Generation 0 is seeded with distinct playstyle profiles instead of the baseline alone.
"""
import argparse
import json
import multiprocessing as mp
import os
import queue
import socket
import sys
import time
import traceback

# Playstyle seeds for generation 0 (all inside the shared strategy bounds).
PROFILES = {
    "Aggressive": dict(det_conf=0.30, aim_gain=1.0, aim_max_step=120, aim_y_offset=0.20, fire_radius_px=45,
                       burst_ms=200, retreat_box_h=450, strafe_mode="none", strafe_period_s=2.0,
                       reload_when_clear_at=2, scan_turn_px=220, target_rule="nearest"),
    "Conservative": dict(det_conf=0.45, aim_gain=0.6, aim_max_step=60, aim_y_offset=0.25, fire_radius_px=25,
                         burst_ms=100, retreat_box_h=160, strafe_mode="alternate", strafe_period_s=1.5,
                         reload_when_clear_at=4, scan_turn_px=140, target_rule="nearest"),
    "Sniper": dict(det_conf=0.40, aim_gain=0.5, aim_max_step=40, aim_y_offset=0.12, fire_radius_px=12,
                   burst_ms=60, retreat_box_h=250, strafe_mode="none", strafe_period_s=2.0,
                   reload_when_clear_at=3, scan_turn_px=100, target_rule="center"),
    "Kiter": dict(det_conf=0.35, aim_gain=0.8, aim_max_step=90, aim_y_offset=0.25, fire_radius_px=35,
                  burst_ms=150, retreat_box_h=220, strafe_mode="alternate", strafe_period_s=1.0,
                  reload_when_clear_at=3, scan_turn_px=180, target_rule="nearest"),
}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def worker_main(slot, jobs_q, out_q, opts):
    os.environ.setdefault("TMPDIR", "D:\\czb\\tmp")
    from bot.worker import Runner
    try:
        r = Runner(opts["map"], opts["config"], max_s=opts["max_s"], worker=opts["worker"], slot=slot,
                   input_mode="js", ocr_gpu=False, latency_s=opts["latency_s"],
                   out_dir=opts["out_dir"] if slot in opts["record_slots"] else None,
                   frame_every=opts["frame_every"])
    except Exception:
        out_q.put(("error", slot, None, traceback.format_exc()))
        return
    out_q.put(("ready", slot, None, None))
    while True:
        job = jobs_q.get()
        if job is None:
            break
        try:
            res = r.run(job)
            out_q.put(("result", slot, job, res))
        except Exception:
            out_q.put(("error", slot, job, traceback.format_exc()))
            try:
                r.game.start()  # try to recover the session for the next job
            except Exception:
                pass
    r.close()


def seeded_state(evo):
    from strategy import Strategy
    state = evo.initial_state()
    for name, p in PROFILES.items():
        sid = name.lower()
        state["strategies"].append(dict(_id=sid, generation=0, persona=name, model=None, parent_ids=[],
                                        params=Strategy(**p).model_dump(), rationale=f"Hand-written {name} profile.",
                                        fallback=False))
        state["jobs"] += [evo.make_job(sid, 0, "baseline") for _ in range(2)]
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--evo-dir", default="D:/czb/evo")
    ap.add_argument("--state", default="D:/czb/evo_state/kino_par.json")
    ap.add_argument("--generations", type=int, default=12)
    ap.add_argument("--map", default="kino")
    ap.add_argument("--max-s", type=float, default=180)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--latency-s", type=float, default=0.11)
    ap.add_argument("--record-slots", default="0", help="comma list of slots that save HUD videos")
    ap.add_argument("--out-dir", default="D:/czb/episodes")
    ap.add_argument("--frame-every", type=float, default=2.0)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.evo_dir))
    import evolution as evo
    from bot.loop import make_client

    os.makedirs(os.path.dirname(os.path.abspath(args.state)), exist_ok=True)
    if os.path.exists(args.state):
        state = evo.load_state(args.state)
        log(f"resumed {args.state}: generation {state['generation']}")
    else:
        state = seeded_state(evo)
        evo.save_state(args.state, state, create=True)
        log(f"new experiment {args.state}: baseline + {len(PROFILES)} profiles, {len(state['jobs'])} jobs")

    opts = dict(map=args.map, config=args.config, max_s=args.max_s, worker=socket.gethostname(),
                latency_s=args.latency_s, out_dir=args.out_dir, frame_every=args.frame_every,
                record_slots={int(x) for x in args.record_slots.split(",") if x != ""})
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()
    job_qs, procs = {}, {}
    for slot in range(args.workers):
        job_qs[slot] = ctx.Queue()
        procs[slot] = ctx.Process(target=worker_main, args=(slot, job_qs[slot], out_q, opts), daemon=True)
        procs[slot].start()
        time.sleep(4)  # stagger Chrome launches / shader compiles

    idle, inflight = set(), {}  # inflight: job_id -> slot
    done = False
    try:
        while not done:
            try:
                kind, slot, job, payload = out_q.get(timeout=600)
            except queue.Empty:
                log(f"no worker message for 10 min; inflight={len(inflight)}")
                continue
            if kind == "ready":
                log(f"slot {slot} ready")
                idle.add(slot)
            elif kind == "result":
                inflight.pop(job["job_id"], None)
                idle.add(slot)
                if payload is None:
                    log(f"slot {slot}: job {job['job_id'][:8]} aborted; will be reissued")
                else:
                    state = evo.ingest_results(state, [payload])
                    evo.save_state(args.state, state)
                    log(f"slot {slot} gen {state['generation']} {job['kind']} {job['strategy_id'][:12]}: "
                        f"+{payload['points']} pts {payload['survival_s']}s "
                        f"fit {payload['points'] + 5 * payload['survival_s']:.0f}")
            elif kind == "error":
                log(f"slot {slot} ERROR{' on job ' + job['job_id'][:8] if job else ''}:\n{payload}")
                if job:
                    inflight.pop(job["job_id"], None)
                    idle.add(slot)
                elif not procs[slot].is_alive() or job is None:
                    pass  # worker failed to start; leave slot out

            # generation finished?
            pending = [j for j in evo.pending_jobs(state) if j["job_id"] not in inflight]
            if not pending and not inflight:
                parents = evo.ranked_parents(state)
                log(f"generation {state['generation']} complete. top: " + json.dumps(
                    [{k: p[k] for k in ('strategy_id', 'mean_fitness', 'evaluation_count') if k in p} for p in parents]))
                if state["generation"] >= args.generations:
                    done = True
                    break
                client, ckind = make_client(args.evo_dir, args.offline)
                if ckind == "luminal":
                    from strategy import Proposal
                    for c in state["strategies"]:
                        if c.get("persona") in ("Bold", "Tuner", "Defender", "Explorer") and not c.get("fallback"):
                            client.cache[c["persona"]] = Proposal(strategy=c["params"], rationale=c["rationale"])
                t = time.time()
                state = evo.advance(state, client)
                evo.save_state(args.state, state)
                log(f"proposed generation {state['generation']} via {ckind} in {time.time() - t:.1f}s")
                for c in state["strategies"]:
                    if c.get("generation") == state["generation"]:
                        log(f"  {c.get('persona', '?'):9s} fallback={c.get('fallback')} changed={c.get('changed_params')} "
                            f"| {str(c.get('rationale'))[:160]}")
                pending = [j for j in evo.pending_jobs(state) if j["job_id"] not in inflight]

            for slot in sorted(idle):
                if not pending:
                    break
                job = pending.pop(0)
                inflight[job["job_id"]] = slot
                idle.discard(slot)
                job_qs[slot].put(job)
    finally:
        for q in job_qs.values():
            q.put(None)
        for p in procs.values():
            p.join(timeout=30)
            if p.is_alive():
                p.terminate()


if __name__ == "__main__":
    main()
