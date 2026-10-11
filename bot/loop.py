"""Closed evolution loop on one game laptop: Jeffrey's evolution state machine + our game worker.

    pending jobs -> play each on the port -> ingest result -> (no jobs left) propose next generation -> repeat

python -m bot.loop --evo-dir D:/czb/evo --state D:/czb/evo_state/kino.json --generations 3
Uses LuminalClient when <evo-dir>/.env has LUMINAL_API_KEY, otherwise the offline local-mutation client
(children are marked fallback=True and must not be presented as Luminal output).
The MongoDB orchestrator can replace this file; don't run both against the same experiment.
"""
import argparse
import json
import os
import sys
import time

from bot.worker import Runner, log


def load_evo(evo_dir):
    sys.path.insert(0, os.path.abspath(evo_dir))
    import evolution
    return evolution


def make_client(evo_dir, offline):
    env = os.path.join(evo_dir, ".env")
    has_key = os.path.exists(env) and "LUMINAL_API_KEY=" in open(env).read() and \
        any(l.startswith("LUMINAL_API_KEY=") and l.strip() != "LUMINAL_API_KEY=" for l in open(env))
    if has_key and not offline:
        from luminal_client import LuminalClient, load_config
        log("using LuminalClient (real agents)")
        return LuminalClient(load_config(env)), "luminal"
    from offline_demo import OfflineClient
    log("WARNING: no Luminal key (or --offline): using OfflineClient local mutations (fallback=True)")
    return OfflineClient(), "offline"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evo-dir", default="D:/czb/evo")
    ap.add_argument("--state", default="D:/czb/evo_state/kino.json")
    ap.add_argument("--generations", type=int, default=3, help="stop after this generation is fully evaluated")
    ap.add_argument("--map", default="kino")
    ap.add_argument("--max-s", type=float, default=180)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    evo = load_evo(args.evo_dir)
    os.makedirs(os.path.dirname(os.path.abspath(args.state)), exist_ok=True)
    if os.path.exists(args.state):
        state = evo.load_state(args.state)
        log(f"resumed {args.state}: generation {state['generation']}")
    else:
        state = evo.initial_state()
        evo.save_state(args.state, state, create=True)
        log(f"new experiment {args.state}: 2 baseline jobs queued")
    client, kind = make_client(args.evo_dir, args.offline)

    runner = Runner(args.map, args.config, max_s=args.max_s)
    try:
        while True:
            jobs = evo.pending_jobs(state)
            if jobs:
                job = jobs[0]
                log(f"gen {state['generation']} job {job['kind']} strategy {job['strategy_id'][:8]} "
                    f"({len(jobs)} pending)")
                r = runner.run(job)
                if r is None:
                    log("aborted; state saved, rerun to resume")
                    break
                state = evo.ingest_results(state, [r])
                evo.save_state(args.state, state)
                continue
            parents = evo.ranked_parents(state)
            log(f"generation {state['generation']} complete. top parents: " +
                json.dumps([{k: p[k] for k in ('strategy_id', 'mean_fitness', 'evaluation_count') if k in p}
                            for p in parents])[:400])
            if state["generation"] >= args.generations:
                break
            t = time.time()
            state = evo.advance(state, client)
            evo.save_state(args.state, state)
            log(f"proposed generation {state['generation']} via {kind} in {time.time() - t:.1f}s")
    finally:
        runner.close()


if __name__ == "__main__":
    main()
