"""Improvement chart for bot/steady.py: champion mean per game (step line) + every game as a dot.

python -m tools.steady_report --state D:/czb/evo_state/steady.json --out D:/czb/renders/steady
"""
import argparse
import json
import os
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SURFACE, TEXT, TEXT_2, MUTED, GRID = "#1a1a19", "#ffffff", "#c3c2b7", "#6b6a64", "#2c2c2a"
SERIES_1 = "#3987e5"   # champion mean
SERIES_3 = "#199e70"   # confirmed promotions


def fit(r):
    return r["points"] + 5 * r["survival_s"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="D:/czb/evo_state/steady.json")
    ap.add_argument("--out", default="D:/czb/renders/steady")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    s = json.load(open(args.state))
    runs = [r for r in s["runs"] if r.get("role") != "seed"]
    hist = s["champion_history"]
    strat = s["strategies"]

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

    ax.scatter([r["iter"] for r in runs], [fit(r) for r in runs], s=60, color=MUTED, edgecolor=SURFACE, linewidth=2,
               zorder=3, label="one 3-min game (any candidate)")
    xs = [h["iter"] for h in hist]
    ys = [h["champion_mean"] for h in hist]
    ax.step(xs, ys, where="post", color=SERIES_1, linewidth=2.5, zorder=4, label="champion's mean fitness")
    promos = [(hist[i]["iter"], hist[i]["champion_mean"], hist[i]["champion_id"])
              for i in range(1, len(hist)) if hist[i]["champion_id"] != hist[i - 1]["champion_id"]]
    for x, y, cid in promos:
        ax.scatter([x], [y], s=110, color=SERIES_3, edgecolor=SURFACE, linewidth=2, zorder=5)
        ax.annotate(f"{strat.get(cid, {}).get('persona', '?')} promoted", (x, y), xytext=(6, 10),
                    textcoords="offset points", color=TEXT, fontsize=11)
    if promos:
        ax.scatter([], [], s=110, color=SERIES_3, label="confirmed challenger becomes champion")
    ax.text(xs[-1] + 0.3, ys[-1], f"champion {ys[-1]:.0f}", color=TEXT, va="center", fontsize=13, fontweight="bold")
    ax.text(xs[0], ys[0], f"baseline {ys[0]:.0f}  ", color=TEXT_2, va="bottom", ha="left", fontsize=12)
    ax.set_xlim(-0.5, max(xs) + 3)
    ax.set_xlabel("game number (each game = one evaluation)", color=TEXT_2, fontsize=12)
    ax.set_ylabel("fitness = points + 5 x survival seconds", color=TEXT_2, fontsize=12)
    ax.set_title("Luminal agents improving the Kino bot, one game at a time", color=TEXT, fontsize=16, loc="left", pad=34)
    leg = ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=11)
    for t in leg.get_texts():
        t.set_color(TEXT_2)
    fig.tight_layout()
    png = os.path.join(args.out, "improvement.png")
    fig.savefig(png, facecolor=SURFACE)

    # summary: first vs current champion, measured
    def stats(sid):
        rs = [r for r in s["runs"] if r["strategy_id"] == sid]
        return dict(games=len(rs), mean_fitness=round(mean(fit(r) for r in rs), 1) if rs else None,
                    mean_points=round(mean(r["points"] for r in rs), 1) if rs else None,
                    mean_survival=round(mean(r["survival_s"] for r in rs), 1) if rs else None,
                    kills=[r["diagnostics"].get("kills") for r in rs])
    first, cur = hist[0]["champion_id"], s["champion_id"]
    lines = ["# Steady-state improvement", "",
             f"Games played: {len(runs)} (plus {len(s['runs']) - len(runs)} seed games). Promotions: {len(promos)}.", "",
             "| | strategy | games | mean fitness | mean points | mean survival s | kills |", "|---|---|---|---|---|---|---|"]
    for label, sid in (("start", first), ("now", cur)):
        st = stats(sid)
        lines.append(f"| {label} | {strat[sid].get('persona')} `{sid[:8]}` | {st['games']} | {st['mean_fitness']} | "
                     f"{st['mean_points']} | {st['mean_survival']} | {st['kills']} |")
    lines += ["", "## Promotions"]
    for x, y, cid in promos:
        c = strat[cid]
        lines.append(f"- game {x}: **{c.get('persona')}** ({'fallback' if c.get('fallback') else c.get('model')}) "
                     f"changed {c.get('changed_params')}: {c.get('rationale')}")
    open(os.path.join(args.out, "report.md"), "w", encoding="utf-8").write("\n".join(lines))
    print(f"[steady_report] {png}; champion {ys[0]} -> {ys[-1]}; promotions {len(promos)}; games {len(runs)}")


if __name__ == "__main__":
    main()
