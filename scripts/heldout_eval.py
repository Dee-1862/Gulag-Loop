"""Fresh-test evaluation: baseline vs the evolved champion on a map never used in training.

    python scripts/heldout_eval.py setup  --source exp-kino-2 --map nacht --games 2
    python -m bot.worker run --experiment heldout-nacht-exp-kino-2 --map nacht --slot 0 --stream --headless --generations 0
    python scripts/heldout_eval.py report --experiment heldout-nacht-exp-kino-2

`setup` creates a MongoDB experiment on the held-out map with two strategies: the hand-written baseline and the
source experiment's champion (copied unchanged), and queues N games of each. No Luminal calls: nothing is tuned
on the held-out map. `report` compares them from the stored runs.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.db import JOBS, PLAYBOOKS, RUNS, get_db  # noqa: E402
from evolution import make_job  # noqa: E402
from mongo_orchestrator import MongoOrchestrator  # noqa: E402


def setup(source, map_name, games):
    db = get_db()
    champ = db[PLAYBOOKS].find_one({"experiment_id": source, "is_best": True})
    if not champ:
        raise SystemExit(f"No champion (is_best) playbook in {source}")
    exp_id = f"heldout-{map_name}-{source}"
    orch = MongoOrchestrator(exp_id)
    if db["experiments"].find_one({"_id": exp_id}):
        raise SystemExit(f"{exp_id} already exists; delete it first or pick another map")
    orch.init_experiment(map_name, notes=f"held-out test of {source}'s champion on {map_name}")
    now = datetime.now(timezone.utc)
    champ_id = f"{exp_id}:champion"
    db[PLAYBOOKS].insert_one({"_id": champ_id, "experiment_id": exp_id, "arm": "bot", "gen": 0, "generation": 0,
                              "persona": f"Champion of {source}", "model": champ.get("model"), "parent": champ["_id"],
                              "parent_ids": [champ["_id"]], "params": champ["params"],
                              "rationale": f"Copied unchanged from {source} ({champ['_id']}); not tuned on {map_name}.",
                              "fallback": False, "is_best": False, "lesson_ids": [], "created_at": now})
    # init queued 2 baseline jobs; top up to `games` each
    base_n = db[JOBS].count_documents({"experiment_id": exp_id, "strategy_id": orch.baseline_id()})
    extra = [make_job(orch.baseline_id(), 0, "heldout_baseline") for _ in range(max(0, games - base_n))]
    extra += [make_job(champ_id, 0, "heldout_champion") for _ in range(games)]
    if extra:
        db[JOBS].insert_many([{**j, "_id": j["job_id"], "experiment_id": exp_id, "created_at": now} for j in extra])
    print(f"created {exp_id}: {games} baseline + {games} champion games on {map_name}")
    print(f"champion = {champ['_id']} from {source}: {champ['params']}")
    return exp_id


def report(exp_id):
    db = get_db()
    rows = list(db[RUNS].aggregate([
        {"$match": {"experiment_id": exp_id}},
        {"$lookup": {"from": PLAYBOOKS, "localField": "strategy_id", "foreignField": "_id", "as": "pb"}},
        {"$group": {"_id": {"$first": "$pb.persona"}, "games": {"$sum": 1}, "survival": {"$avg": "$survival_s"},
                    "fitness": {"$avg": "$fitness"}, "points": {"$avg": "$points"},
                    "no_target": {"$avg": "$diagnostics.time_no_target_pct"},
                    "det": {"$avg": "$diagnostics.det_count_avg"},
                    "seen": {"$avg": "$diagnostics.first_zombie_seen_s"}}},
        {"$sort": {"_id": 1}}]))
    print(f"held-out results: {exp_id}")
    print(f"{'strategy':<28} {'games':>5} {'survival s':>10} {'fitness':>8} {'points':>6} {'no-target %':>11} "
          f"{'det/frame':>9} {'1st seen s':>10}")
    for r in rows:
        print(f"{str(r['_id']):<28} {r['games']:>5} {r['survival']:>10.1f} {r['fitness']:>8.1f} {r['points']:>6.0f} "
              f"{(r['no_target'] or 0):>11.1f} {(r['det'] or 0):>9.2f} {(r['seen'] or 0):>10.1f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--source", required=True)
    s.add_argument("--map", default="nacht")
    s.add_argument("--games", type=int, default=2)
    r = sub.add_parser("report")
    r.add_argument("--experiment", required=True)
    a = ap.parse_args()
    setup(a.source, a.map, a.games) if a.cmd == "setup" else report(a.experiment)
