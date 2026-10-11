"""Rebuild every result in the README straight from MongoDB.

    python scripts/reproduce_chart.py                    # latest experiment
    python scripts/reproduce_chart.py --experiment exp-1
    python scripts/reproduce_chart.py --selftest         # check the script on throwaway data

Writes results/heldout_by_generation.png and results/RESULTS.md. Nothing here is
typed by hand: if a number is in the README, it came from this query.
"""
import argparse
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common.db import EXPERIMENTS, GENERATIONS, LESSONS, get_db

ROOT = Path(__file__).resolve().parent.parent
ARM_LABELS = {"A": "A · no memory", "B": "B · naive memory", "C": "C · validated memory"}
# Reference palette slots 1-3 (validated together for every pair, light mode)
ARM_COLORS = {"A": "#2a78d6", "B": "#eb6834", "C": "#1baf7a"}
INK, INK_2, GRID, SURFACE, BASELINE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb", "#8a8984"


def load(db, experiment_id):
    exp = (db[EXPERIMENTS].find_one({"_id": experiment_id}) if experiment_id
           else db[EXPERIMENTS].find_one(sort=[("started_at", -1)]))
    if not exp:
        raise SystemExit("No experiment found. Run the evolution loop first.")
    rows = list(db[GENERATIONS].find({"experiment_id": exp["_id"]}).sort([("arm", 1), ("gen", 1)]))
    if not rows:
        raise SystemExit(f"Experiment {exp['_id']} has no generations yet.")
    by_arm = {}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)
    planted = list(db[LESSONS].find({"planted": True, "experiment_id": exp["_id"]},
                                    {"_id": 1, "arm": 1, "text": 1, "status": 1, "retired_gen": 1}))
    return exp, by_arm, planted


def baseline_of(by_arm):
    gen0 = [r["heldout_mean"] for rows in by_arm.values() for r in rows if r["gen"] == 0]
    return sum(gen0) / len(gen0) if gen0 else None


def draw(exp, by_arm, out_png):
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    base = baseline_of(by_arm)
    if base is not None:
        ax.axhline(base, color=BASELINE, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
        ax.text(0.99, base, "generation-0 baseline ", transform=ax.get_yaxis_transform(),
                va="top", ha="right", fontsize=8.5, color=INK_2)
    last_x = 0
    for arm in [a for a in ("A", "B", "C") if a in by_arm]:
        rows = by_arm[arm]
        xs = [r["gen"] for r in rows]
        ys = [r["heldout_mean"] for r in rows]
        lo = [r.get("ci", [y, y])[0] for r, y in zip(rows, ys)]
        hi = [r.get("ci", [y, y])[1] for r, y in zip(rows, ys)]
        c = ARM_COLORS[arm]
        ax.fill_between(xs, lo, hi, color=c, alpha=0.12, linewidth=0, zorder=2)
        ax.plot(xs, ys, color=c, linewidth=2, zorder=3, label=ARM_LABELS[arm])
        ax.plot(xs[-1], ys[-1], "o", color=c, markersize=6, markeredgecolor=SURFACE,
                markeredgewidth=2, zorder=4)
        ax.annotate(f"{arm}  {ys[-1]:.1f}", (xs[-1], ys[-1]), xytext=(8, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)
        last_x = max(last_x, xs[-1])
    ax.set_xlim(-0.2, last_x + 1.1)
    ax.set_xticks(range(0, last_x + 1))
    ax.set_xlabel("Generation", color=INK_2, fontsize=9)
    ax.set_ylabel("Mean round reached, held-out seeds", color=INK_2, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8.5, length=0)
    ax.legend(loc="upper left", bbox_to_anchor=(0, 1.13), ncol=3, frameon=False, fontsize=8.5,
              labelcolor=INK)
    fig.suptitle(f"Held-out score by generation · {exp['_id']}", x=0.01, ha="left",
                 fontsize=11, fontweight="semibold", color=INK, y=1.02)
    fig.text(0.01, -0.02, "Bands: 95% bootstrap interval over held-out seeds. Source: MongoDB "
             "collection `generations`, rebuilt by scripts/reproduce_chart.py.",
             fontsize=7.5, color=INK_2)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def write_table(exp, by_arm, planted, out_md, png_name):
    base = baseline_of(by_arm)
    lines = [
        f"# Results · {exp['_id']}",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `scripts/reproduce_chart.py` "
        "from MongoDB. Do not edit by hand.",
        "",
        f"![Held-out score by generation]({png_name})",
        "",
        f"Settings: model `{exp.get('model', '?')}`, temperature {exp.get('temperature', '?')}, "
        f"{exp.get('proposals', '?')} proposals per generation, "
        f"{len(exp.get('train_seeds', []))} training seeds, {len(exp.get('heldout_seeds', []))} held-out seeds. "
        f"Predictions committed before running: `{exp.get('predictions_commit', 'not recorded')}`.",
        "",
        "| Arm | Generations | Final held-out mean | 95% interval | Gain over baseline | Mean over all generations |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for arm in [a for a in ("A", "B", "C") if a in by_arm]:
        rows = by_arm[arm]
        final = rows[-1]
        ci = final.get("ci", [final["heldout_mean"]] * 2)
        gain = f"{final['heldout_mean'] - base:+.2f}" if base is not None else "n/a"
        auc = sum(r["heldout_mean"] for r in rows) / len(rows)
        lines.append(f"| {ARM_LABELS[arm]} | {final['gen']} | {final['heldout_mean']:.2f} | "
                     f"{ci[0]:.2f} to {ci[1]:.2f} | {gain} | {auc:.2f} |")
    if planted:
        lines += ["", "## Planted bad lessons", "",
                  "| Arm | Lesson | Status | Retired at generation |", "| --- | --- | --- | --- |"]
        for p in sorted(planted, key=lambda p: (p["arm"], p["text"])):
            lines.append(f"| {p['arm']} | {p['text']} | {p['status']} | {p.get('retired_gen') or 'not retired'} |")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def selftest(db):
    """Throwaway synthetic data, clearly labelled, deleted afterwards. Checks the script only."""
    exp_id = f"selftest-{datetime.now(timezone.utc):%H%M%S}"
    rng = random.Random(1)
    db[EXPERIMENTS].insert_one({"_id": exp_id, "arms": ["A", "B", "C"], "model": "SYNTHETIC",
                                "temperature": 0.9, "proposals": 6, "train_seeds": list(range(20)),
                                "heldout_seeds": list(range(20, 40)), "predictions_commit": "SYNTHETIC",
                                "started_at": datetime.now(timezone.utc)})
    slope = {"A": 0.25, "B": 0.15, "C": 0.45}
    docs = []
    for arm in "ABC":
        for g in range(9):
            m = 9.0 + slope[arm] * g + rng.uniform(-0.3, 0.3) if g else 9.0
            docs.append({"experiment_id": exp_id, "arm": arm, "gen": g, "heldout_mean": m,
                         "ci": [m - 0.6, m + 0.6], "train_mean": m + 0.5})
    db[GENERATIONS].insert_many(docs)
    try:
        exp, by_arm, planted = load(db, exp_id)
        out = ROOT / "results" / "selftest"
        draw(exp, by_arm, out / "heldout_by_generation.png")
        write_table(exp, by_arm, planted, out / "RESULTS.md", "heldout_by_generation.png")
        print(f"selftest ok: wrote {out} (SYNTHETIC data, not a result)")
    finally:
        db[GENERATIONS].delete_many({"experiment_id": exp_id})
        db[EXPERIMENTS].delete_one({"_id": exp_id})


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--experiment")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args()
    db = get_db()
    if args.selftest:
        selftest(db)
    else:
        exp, by_arm, planted = load(db, args.experiment)
        out = ROOT / "results"
        draw(exp, by_arm, out / "heldout_by_generation.png")
        write_table(exp, by_arm, planted, out / "RESULTS.md", "heldout_by_generation.png")
        print(f"wrote {out / 'heldout_by_generation.png'} and {out / 'RESULTS.md'}")
