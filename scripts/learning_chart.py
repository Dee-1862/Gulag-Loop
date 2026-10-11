"""Does the evolved bot learn to go for the head? One chart per experiment, straight from MongoDB.

    python scripts/learning_chart.py --experiment exp-kino-1
    python scripts/learning_chart.py --selftest      # synthetic data, checks the script only

Top panel: estimated headshot share of kills and share of shots fired at the head zone, per generation.
Bottom panel: aim_y_offset the agents chose (0 = top of the zombie box), with the head zone shaded.
Writes results/learning_<experiment>.png.
"""
import argparse
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from bot.scoring import HEAD_ZONE_FRAC
from common.db import EXPERIMENTS, PLAYBOOKS, RUNS, get_db

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK_2, GRID, SURFACE, BAND = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb", "#f3d4d4"


def per_generation(db, exp_id):
    """Mean headshot share, head-aim share and aim_y_offset of the strategies run in each generation."""
    return list(db[RUNS].aggregate([
        {"$match": {"experiment_id": exp_id}},
        {"$lookup": {"from": PLAYBOOKS, "localField": "strategy_id", "foreignField": "_id", "as": "pb"}},
        {"$set": {"aim_y": {"$first": "$pb.params.aim_y_offset"}}},
        {"$group": {"_id": "$generation",
                    "headshot_rate": {"$avg": "$diagnostics.headshot_rate_est"},
                    "head_aim_pct": {"$avg": "$diagnostics.head_aim_fire_pct"},
                    "aim_y": {"$avg": "$aim_y"}, "aim_y_min": {"$min": "$aim_y"}, "aim_y_max": {"$max": "$aim_y"},
                    "kills": {"$sum": "$diagnostics.kills_est"}, "runs": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))


def draw(rows, exp_id, out):
    gens = [r["_id"] for r in rows]
    fig, (top, bot) = plt.subplots(2, 1, figsize=(8, 6), dpi=150, sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2], "hspace": 0.12})
    fig.patch.set_facecolor(SURFACE)
    for ax in (top, bot):
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=INK_2, labelsize=8.5, length=0)

    def series(key, scale):
        pts = [(g, r[key] * scale) for g, r in zip(gens, rows) if r.get(key) is not None]
        return [p[0] for p in pts], [p[1] for p in pts]

    for key, scale, color, label in (("headshot_rate", 100, BLUE, "Headshot share of kills (est.)"),
                                     ("head_aim_pct", 1, ORANGE, "Shots fired at the head zone")):
        xs, ys = series(key, scale)
        if xs:
            top.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=5,
                     markeredgecolor=SURFACE, markeredgewidth=1.5, label=label)
            top.annotate(f"{ys[-1]:.0f}%", (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points",
                         va="center", fontsize=9, color=INK)
    top.set_ylim(0, 100)
    top.set_ylabel("% of kills / shots", color=INK_2, fontsize=9)
    top.legend(loc="upper left", frameon=False, fontsize=8.5, labelcolor=INK, ncol=2, bbox_to_anchor=(0, 1.12))

    bot.axhspan(0, HEAD_ZONE_FRAC, color=BAND, zorder=0)
    bot.text(gens[0] if gens else 0, HEAD_ZONE_FRAC / 2, "  head zone", va="center", fontsize=8, color=INK_2)
    xs, ys = series("aim_y", 1)
    lo = [r["aim_y_min"] for r in rows if r.get("aim_y") is not None]
    hi = [r["aim_y_max"] for r in rows if r.get("aim_y") is not None]
    if xs:
        bot.vlines(xs, lo, hi, color=INK_2, linewidth=1)
        bot.plot(xs, ys, color=INK, linewidth=2, marker="o", markersize=5, markeredgecolor=SURFACE)
    bot.set_ylim(0.5, 0)  # top of the zombie box at the top of the axis
    bot.set_ylabel("aim_y_offset\n(0 = top of box)", color=INK_2, fontsize=9)
    bot.set_xlabel("Generation", color=INK_2, fontsize=9)
    bot.set_xticks(gens)

    fig.suptitle(f"Learning to aim for the head · {exp_id}", x=0.01, ha="left", fontsize=11,
                 fontweight="semibold", color=INK, y=0.99)
    fig.text(0.01, 0.005, "Kill types estimated from points-counter changes (headshot 100, torso 60, limb 50, "
             "neck 70). Bars: range of aim_y_offset across strategies run. Source: MongoDB runs + playbooks.",
             fontsize=7, color=INK_2)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def selftest(db):
    """Insert clearly labelled synthetic runs, draw, delete."""
    exp_id = f"selftest-learning-{datetime.now(timezone.utc):%H%M%S}"
    rng = random.Random(2)
    db[EXPERIMENTS].insert_one({"_id": exp_id, "design": "SYNTHETIC", "map": "kino", "generation": 6,
                                "started_at": datetime.now(timezone.utc)})
    try:
        for g in range(7):
            for i in range(5):
                aim = max(0.03, 0.30 - 0.04 * g + rng.uniform(-0.05, 0.05))
                pid = f"{exp_id}-{g}-{i}"
                db[PLAYBOOKS].insert_one({"_id": pid, "experiment_id": exp_id, "generation": g,
                                          "params": {"aim_y_offset": aim}})
                head = max(0.0, min(1.0, 0.1 + 0.11 * g + rng.uniform(-0.08, 0.08)))
                db[RUNS].insert_one({"_id": pid, "experiment_id": exp_id, "strategy_id": pid, "generation": g,
                                     "diagnostics": {"headshot_rate_est": head, "kills_est": 8,
                                                     "head_aim_fire_pct": 100 * min(1, head + 0.1)}})
        out = ROOT / "results" / "selftest" / "learning_selftest.png"
        draw(per_generation(db, exp_id), exp_id + " (SYNTHETIC)", out)
        print(f"selftest ok: {out} (synthetic data, not a result)")
    finally:
        for c in (RUNS, PLAYBOOKS):
            db[c].delete_many({"experiment_id": exp_id})
        db[EXPERIMENTS].delete_one({"_id": exp_id})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    db = get_db()
    if args.selftest:
        selftest(db)
    else:
        if not args.experiment:
            raise SystemExit("--experiment is required")
        rows = per_generation(db, args.experiment)
        if not rows:
            raise SystemExit(f"No runs for {args.experiment} yet")
        out = ROOT / "results" / f"learning_{args.experiment}.png"
        draw(rows, args.experiment, out)
        print(f"wrote {out}")
