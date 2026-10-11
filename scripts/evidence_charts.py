"""Charts that show the results are not a lucky run, straight from MongoDB.

    python scripts/evidence_charts.py

Writes docs/img/evidence_generations.png: every game as a dot, the generation mean as a line, per experiment.
Same strategy replayed in the same experiment shows how noisy a single game is, which is why we compare means
and re-run the champion every generation.
"""
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from common.db import RUNS, get_db

EXPERIMENTS = [("exp-kino-2", "exp-kino-2: Luminal agents, 3 generations"),
               ("rishit-kino_v6", "rishit-kino_v6: 5 hand-made styles, then evolved")]


def load(exp):
    rows = list(get_db()[RUNS].find({"experiment_id": exp}, {"generation": 1, "fitness": 1, "strategy_id": 1}))
    return [(r.get("generation") or 0, float(r["fitness"]), r["strategy_id"]) for r in rows if r.get("fitness") is not None]


def main():
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
    for ax, (exp, title) in zip(axes[:2], EXPERIMENTS):
        rows = load(exp)
        gens = sorted({g for g, _, _ in rows})
        for g in gens:
            ys = [f for gg, f, _ in rows if gg == g]
            xs = [g + (i - (len(ys) - 1) / 2) * 0.06 for i in range(len(ys))]
            ax.scatter(xs, ys, s=28, color="#8aa0b8", zorder=2)
        means = [statistics.mean(f for gg, f, _ in rows if gg == g) for g in gens]
        ax.plot(gens, means, color="#d9480f", marker="o", lw=2, zorder=3, label="generation mean")
        ax.set_xticks(gens)
        ax.set_xlabel("generation")
        ax.set_ylabel("fitness (points + 5 x survival s)")
        ax.set_title(title, fontsize=10)
        ax.legend(frameon=False)
    # same strategy, repeated games: the noise floor
    ax = axes[2]
    by = {}
    for exp, _ in EXPERIMENTS:
        for _, f, s in load(exp):
            by.setdefault((exp, s), []).append(f)
    reps = sorted(((k, v) for k, v in by.items() if len(v) >= 2), key=lambda kv: -max(kv[1]) + min(kv[1]))[:8]
    for i, ((exp, s), v) in enumerate(reps):
        ax.plot([i, i], [min(v), max(v)], color="#8aa0b8", lw=3, zorder=1)
        ax.scatter([i] * len(v), v, color="#d9480f", s=26, zorder=2)
    ax.set_xticks(range(len(reps)))
    ax.set_xticklabels([k[1].split(":")[-1][:9] for k, _ in reps], rotation=40, ha="right", fontsize=8)
    ax.set_ylabel("fitness")
    ax.set_title("Same strategy, repeated games:\none game can mislead", fontsize=10)
    fig.tight_layout()
    out = ROOT / "docs" / "img" / "evidence_generations.png"
    fig.savefig(out, dpi=130)
    print("wrote", out)


if __name__ == "__main__":
    main()
