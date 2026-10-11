"""Evolution progress report from the evolve_local state file: chart PNG + lineage/rationale text.

python -m tools.evo_report --state D:/czb/evo_state/kino.json --out D:/czb/renders/evo
"""
import argparse
import json
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# reference palette, dark mode (video background)
SURFACE = "#1a1a19"
TEXT = "#ffffff"
TEXT_2 = "#c3c2b7"
MUTED = "#6b6a64"
GRID = "#2c2c2a"
SERIES_1 = "#3987e5"


def fitness(r):
    return r["points"] + 5 * r["survival_s"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="D:/czb/evo_state/kino.json")
    ap.add_argument("--out", default="D:/czb/renders/evo")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    s = json.load(open(args.state))
    strategies = {c["_id"]: c for c in s["strategies"]}
    jobs = {j["job_id"]: j for j in s["jobs"]}

    # evaluations by the generation in which they were run (job generation)
    pts = []
    by_strategy = defaultdict(list)
    for r in s["runs"]:
        g = jobs.get(r["job_id"], {}).get("generation", 0)
        pts.append((g, fitness(r), r["strategy_id"]))
        by_strategy[r["strategy_id"]].append((g, fitness(r)))
    gens = sorted({g for g, _, _ in pts})

    # best mean fitness so far (only strategies with >= 1 eval up to that generation)
    best_curve = []
    for G in gens:
        means = {}
        for sid, evals in by_strategy.items():
            e = [f for g, f in evals if g <= G]
            if e:
                means[sid] = sum(e) / len(e)
        best_curve.append(max(means.values()))
    base = [f for g, f, sid in pts if sid == "baseline"]
    base_mean = sum(base) / len(base) if base else None

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=TEXT_2, labelsize=12)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)

    ax.scatter([g for g, _, _ in pts], [f for _, f, _ in pts], s=70, color=MUTED, edgecolor=SURFACE,
               linewidth=2, zorder=3, label="single 3-min evaluation")
    ax.plot(gens, best_curve, color=SERIES_1, linewidth=2.5, zorder=4, marker="o", markersize=9,
            markeredgecolor=SURFACE, markeredgewidth=2, label="best mean fitness so far")
    if base_mean is not None:
        ax.axhline(base_mean, color=TEXT_2, linewidth=1.2, linestyle=(0, (4, 4)), zorder=2)
        ax.text(gens[-1] + 0.15, base_mean, f"  baseline mean {base_mean:.0f}", color=TEXT_2, va="bottom", fontsize=12,
                bbox=dict(facecolor=SURFACE, edgecolor="none", pad=1))
    ax.text(gens[-1] + 0.15, best_curve[-1], f"best {best_curve[-1]:.0f}", color=TEXT, va="center",
            fontsize=13, fontweight="bold")
    ax.set_xticks(gens)
    ax.set_xticklabels([f"gen {g}" for g in gens])
    ax.set_xlim(min(gens) - 0.4, max(gens) + 1.3)
    ax.set_ylabel("fitness = points + 5 x survival seconds", color=TEXT_2, fontsize=12)
    ax.set_title("Luminal agents evolving the Kino bot (browser port, on-device vision)", color=TEXT,
                 fontsize=16, loc="left", pad=34)
    leg = ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, frameon=False, fontsize=12)
    for t in leg.get_texts():
        t.set_color(TEXT_2)
    fig.tight_layout()
    png = os.path.join(args.out, "fitness.png")
    fig.savefig(png, facecolor=SURFACE)

    # champion lineage + rationales
    ranked = sorted(((sum(f for _, f in e) / len(e), len(e), sid) for sid, e in by_strategy.items()), reverse=True)
    lines = ["# Evolution report", "", "| rank | strategy | persona | gen | mean fitness | evals | source |",
             "|---|---|---|---|---|---|---|"]
    for i, (m, n, sid) in enumerate(ranked[:8], 1):
        c = strategies.get(sid, {})
        src = "baseline" if sid == "baseline" else ("local mutation" if c.get("fallback") else c.get("model") or "?")
        lines.append(f"| {i} | `{sid[:8]}` | {c.get('persona', '-')} | {c.get('generation', 0)} | {m:.0f} | {n} | {src} |")
    champ = ranked[0][2]
    lines += ["", f"## Lineage of the champion `{champ[:8]}`", ""]
    sid = champ
    while sid and sid != "baseline" and sid in strategies:
        c = strategies[sid]
        lines.append(f"- **gen {c.get('generation')} {c.get('persona')}** (`{sid[:8]}`, changed {c.get('changed_params')}): "
                     f"{c.get('rationale')}")
        sid = (c.get("parent_ids") or [None])[0]
    lines.append("- **baseline** (hand-written, deliberately mediocre)")
    lines += ["", "## All agent rationales", ""]
    for c in sorted(s["strategies"], key=lambda c: (c.get("generation", 0), c.get("persona", ""))):
        if c.get("generation"):
            tag = "local mutation" if c.get("fallback") else c.get("model")
            lines.append(f"- gen {c['generation']} **{c.get('persona')}** [{tag}]: {c.get('rationale')}")
    md = os.path.join(args.out, "report.md")
    open(md, "w", encoding="utf-8").write("\n".join(lines))
    print(f"[report] {png}\n[report] {md}\n[report] best curve {[round(b) for b in best_curve]} "
          f"baseline mean {base_mean and round(base_mean)} runs {len(pts)}")


if __name__ == "__main__":
    main()
