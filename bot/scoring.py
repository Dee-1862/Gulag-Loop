"""Black Ops 1 Zombies scoring, used to tell what the bot hit from the points counter.

Point values from the Call of Duty Wiki "Points" article (World at War to Black Ops III):
non-lethal hit 10; lethal headshot 100, neck 70, torso 60, limb 50; melee kill 130.

The points counter is read about once a second, so one reading can hold several events.
classify_delta() picks the explanation with the fewest events. It is an ESTIMATE:
100 could be one headshot or ten hits; one event is far more likely at round-1 fire rates.
"""
from collections import Counter
from itertools import combinations_with_replacement

HIT_POINTS = 10
KILL_POINTS = {"torso": 60, "head": 100, "limb": 50, "neck": 70, "melee": 130}  # order = tie-break preference
HEAD_ZONE_FRAC = 0.15   # top 15% of a standing zombie's box is the head
HEAD_ZONE_WIDTH = 0.6   # central 60% of the box width


def classify_delta(delta, max_kills=3):
    """Counter of hits and kill types that best explains one increase in points."""
    if delta <= 0 or delta % 10:
        return Counter({"unexplained_points": max(delta, 0)})
    best = None
    kinds = list(KILL_POINTS)
    for k in range(max_kills + 1):
        for combo in combinations_with_replacement(kinds, k):
            rest = delta - sum(KILL_POINTS[c] for c in combo)
            if rest < 0:
                continue
            hits = rest // HIT_POINTS
            key = (len(combo) + hits, len(combo), [kinds.index(c) for c in combo])
            if best is None or key < best[0]:
                best = (key, combo, hits)
    _, combo, hits = best
    out = Counter(f"kill_{c}" for c in combo)
    if hits:
        out["hit"] = hits
    return out


def in_head_zone(box, x, y):
    """True if screen point (x, y) is on the box's head zone."""
    if box.h <= 0:
        return False
    rel_y = (y - box.y1) / box.h
    return 0 <= rel_y <= HEAD_ZONE_FRAC and abs(x - box.cx) <= HEAD_ZONE_WIDTH * box.w / 2


def summarize(tally, head_fires, fires):
    """Diagnostics for the agents, all marked _est because they come from OCR deltas."""
    kills = sum(v for k, v in tally.items() if k.startswith("kill_"))
    kill_points = sum(KILL_POINTS[k[5:]] * v for k, v in tally.items() if k.startswith("kill_"))
    return {
        "kills_est": kills,
        "headshot_kills_est": tally.get("kill_head", 0),
        "body_kills_est": tally.get("kill_torso", 0) + tally.get("kill_neck", 0) + tally.get("kill_limb", 0),
        "melee_kills_est": tally.get("kill_melee", 0),
        "headshot_rate_est": round(tally.get("kill_head", 0) / kills, 3) if kills else None,
        "points_per_kill_est": round(kill_points / kills, 1) if kills else None,
        "non_lethal_hits_est": tally.get("hit", 0),
        "head_aim_fire_pct": round(100.0 * head_fires / fires, 1) if fires else None,
    }
