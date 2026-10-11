"""Team-wide settings in MongoDB, so every laptop reads one place instead of its own files.

machines:   one row per laptop (hostname): mouse calibration, GPU, when it was measured.
            A laptop without its own row uses the team default (median of all rows).
strategies: named playstyles (from strategies.json) plus a virtual "champion" = the current best
            evolved strategy in MongoDB experiments.

calibration.json and strategies.json stay as offline fallbacks.

    python -m common.machines list
    python -m common.machines publish-strategies zombie_bot/strategies.json
    python -m common.machines set-calibration --cpp 0.6794 [--machine NAME]
    python -m common.machines strategy champion
"""
import argparse
import json
import socket
import statistics
from datetime import datetime, timezone

from .db import PLAYBOOKS, RUNS, get_db

MACHINES = "machines"
STRATEGIES = "strategies"


def machine_name():
    return socket.gethostname()


def _now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ calibration

def save_calibration(counts_per_px, machine=None, **extra):
    """Store this laptop's calibration (also called by `bot.worker calibrate`)."""
    machine = machine or machine_name()
    get_db()[MACHINES].update_one(
        {"_id": machine},
        {"$set": {"counts_per_px": round(float(counts_per_px), 4), "calibrated_at": _now(), **extra}},
        upsert=True)
    return machine


def get_calibration(machine=None):
    """This laptop's calibration, else the team default (median of all laptops), else None."""
    machine = machine or machine_name()
    db = get_db()
    row = db[MACHINES].find_one({"_id": machine, "counts_per_px": {"$gt": 0}})
    if row:
        return {"counts_per_px": row["counts_per_px"], "source": f"MongoDB machines/{machine}"}
    vals = [r["counts_per_px"] for r in db[MACHINES].find({"counts_per_px": {"$gt": 0}}, {"counts_per_px": 1})]
    if vals:
        return {"counts_per_px": round(statistics.median(vals), 4),
                "source": f"team default (median of {len(vals)} laptop{'s' if len(vals) > 1 else ''})"}
    return None


def team_table():
    return list(get_db()[MACHINES].find({}, {"_id": 1, "counts_per_px": 1, "gpu": 1, "calibrated_at": 1})
                .sort("calibrated_at", -1))


# ------------------------------------------------------------------ strategies

def publish_strategies(library, source="strategies.json"):
    """Upsert every named strategy from a strategies.json-style dict. Returns the names."""
    db = get_db()
    for name, entry in library.items():
        entry = entry if isinstance(entry, dict) else {"params": entry}
        doc = {"params": entry.get("params"), "persona": entry.get("persona"), "measured": entry.get("measured"),
               "alias_of": entry.get("alias_of"), "source": entry.get("source") or source, "updated_at": _now()}
        db[STRATEGIES].update_one({"_id": name}, {"$set": {k: v for k, v in doc.items() if v is not None}},
                                  upsert=True)
    return list(library)


def champion(map_name="kino"):
    """Most recent champion (is_best) playbook from MongoDB experiments, with its measured mean fitness."""
    db = get_db()
    exp_ids = [e["_id"] for e in db["experiments"].find({"map": map_name}, {"_id": 1})]
    pb = db[PLAYBOOKS].find_one({"is_best": True, "experiment_id": {"$in": exp_ids}}, sort=[("created_at", -1)])
    if not pb:
        return None
    stats = next(db[RUNS].aggregate([{"$match": {"strategy_id": pb["_id"]}},
                                     {"$group": {"_id": None, "mean": {"$avg": "$fitness"}, "n": {"$sum": 1}}}]),
                 None)
    return {"name": "champion", "params": pb["params"], "persona": pb.get("persona"),
            "source": f"MongoDB {pb['experiment_id']} playbook {pb['_id']}",
            "measured": {"mean_fitness": round(stats["mean"], 1), "runs": stats["n"]} if stats else None}


def get_strategy(name, map_name="kino"):
    """Params and metadata for a named strategy; 'champion' = best evolved in MongoDB. None if unknown."""
    if name == "champion":
        return champion(map_name)
    db = get_db()
    row = db[STRATEGIES].find_one({"_id": name})
    seen = set()
    while row and row.get("alias_of") and row["_id"] not in seen:  # "best" -> "aggressive"
        seen.add(row["_id"])
        target = db[STRATEGIES].find_one({"_id": row["alias_of"]})
        if not target:
            break
        row = {**target, "params": row.get("params") or target.get("params")}
    if not row or not row.get("params"):
        return None
    return {"name": name, "params": row["params"], "persona": row.get("persona"),
            "measured": row.get("measured"), "source": f"MongoDB strategies/{name}"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pub = sub.add_parser("publish-strategies")
    pub.add_argument("path")
    cal = sub.add_parser("set-calibration")
    cal.add_argument("--cpp", type=float, required=True)
    cal.add_argument("--machine", default=None)
    st = sub.add_parser("strategy")
    st.add_argument("name")
    args = ap.parse_args()
    if args.cmd == "list":
        print("machines:")
        for r in team_table():
            print(f"  {r['_id']:<24} counts_per_px={r.get('counts_per_px')}  gpu={r.get('gpu', '?')}  "
                  f"{r.get('calibrated_at', '')}")
        print("this laptop uses:", get_calibration())
        print("strategies:", sorted(s["_id"] for s in get_db()[STRATEGIES].find({}, {"_id": 1})) + ["champion"])
    elif args.cmd == "publish-strategies":
        names = publish_strategies(json.load(open(args.path)), source=args.path)
        print(f"published {len(names)} strategies: {names}")
    elif args.cmd == "set-calibration":
        print("saved for", save_calibration(args.cpp, args.machine, source="manual"))
    elif args.cmd == "strategy":
        print(json.dumps(get_strategy(args.name), indent=2, default=str))


if __name__ == "__main__":
    main()
