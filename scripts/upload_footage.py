"""Upload episode videos already on disk to MongoDB (GridFS 'footage') for one experiment.

    python scripts/upload_footage.py --experiment exp-kino-2 [--dir runtime/episodes]

Matches <dir>/<job_id>.mp4 to the experiment's runs, re-encodes to ~0.5 MB (640 px, H.264) and tags each video
with generation, points, survival and fitness. Already uploaded videos are skipped.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.paths import p as data_path  # noqa: E402
from common.db import get_db  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--dir", default=data_path("episodes"))
    args = ap.parse_args()
    from bot.publish import Publisher
    pub = Publisher(args.experiment)
    done = skipped = missing = 0
    for r in get_db()["runs"].find({"experiment_id": args.experiment}).sort("completed_at", 1):
        path = os.path.join(args.dir, f"{r['job_id']}.mp4")
        if not os.path.exists(path):
            missing += 1
            continue
        meta = {k: r.get(k) for k in ("strategy_id", "generation", "points", "survival_s", "fitness", "worker")}
        meta["kills"] = (r.get("diagnostics") or {}).get("kills_est")
        if pub.upload_footage(r["job_id"], path, meta=meta):
            done += 1
            print(f"uploaded gen {meta['generation']} {r['job_id'][:8]} ({meta['survival_s']} s, fitness {meta['fitness']})")
        else:
            skipped += 1
    print(f"done: {done} uploaded, {skipped} already there, {missing} without a local video")
