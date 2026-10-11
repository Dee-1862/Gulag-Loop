"""Rule-based tactics: which zombie to fight, and how to move while fighting. Deterministic, no learning.

Called once per frame by the controller. The controller keeps its own aiming math; tactics only
  1. picks the target by THREAT across all confirmed zombies (not just the strategy's target_rule),
  2. adjusts the aim point for close zombies (chest, since the pitch cap can't reach a close head),
  3. detects a blocked engagement (aim error not shrinking, or firing with no points) and side-steps
     to get a new angle,
  4. kites: backs off AND strafes away from the other zombies when one is close,
  5. handles being swarmed (3+ close zombies).

Rules (all numbers in 720p frame units; h = box height / frame height):
  threat = 3*h + 2*approach + 0.5*aimability + 0.3 if current target
  close zombie   h >= CLOSE_H        -> aim at the chest (aim_y >= 0.35), kite
  blocked        engaged >= 1.5 s and error not shrinking by 25%, or firing >= 1.2 s with no points
                 -> REPOSITION: strafe 0.6 s toward the side with fewer zombies, then re-aim (1.5 s cooldown)
  swarmed        >= 3 zombies with h >= SWARM_H -> back off + strafe away from their centre, nearest first
"""
from collections import deque

CLOSE_H = 0.40      # box height fraction: zombie is close
SWARM_H = 0.30      # zombies at least this close count toward a swarm
SWARM_N = 3
NO_PROGRESS_S = 1.5
FIRE_NO_POINTS_S = 1.2
REPOSITION_S = 0.6
REPOSITION_COOLDOWN_S = 1.5


class Decision:
    __slots__ = ("target", "err_x", "err_y", "keys", "label", "threat")

    def __init__(self):
        self.target = None
        self.err_x = self.err_y = 0.0
        self.keys = {"w": False, "s": False, "a": False, "d": False}
        self.label = None
        self.threat = 0.0


class Tactics:
    def __init__(self):
        self.h_hist = {}            # track id -> deque[(t, h)]
        self.cur_id = None
        self.engage_t0 = None       # when the current target was first engaged
        self.err0 = None            # aim error at engagement start
        self.fire_t0 = None         # first shot at the current target
        self.points_at_fire = None
        self.repo_until = 0.0
        self.repo_key = "a"
        self.repo_cooldown = 0.0
        self.stats = {"target_switches": 0, "repositions": 0, "kite_frames": 0, "swarm_frames": 0,
                      "chest_aim_frames": 0, "max_close_zombies": 0}

    # ------------------------------------------------------------------ selection
    def _approach(self, tid, h, now):
        hist = self.h_hist.setdefault(tid, deque(maxlen=8))
        hist.append((now, h))
        if len(hist) < 3 or hist[-1][0] - hist[0][0] < 0.15:
            return 0.0
        rate = (hist[-1][1] - hist[0][1]) / (hist[-1][0] - hist[0][0])  # h per second, + = coming closer
        return max(0.0, min(1.0, rate * 2.0))

    def update(self, obs, now, W, H, S, points):
        """Pick the target and movement for this frame. Returns a Decision."""
        d = Decision()
        confirmed = [t for t in obs.tracks if getattr(t, "hits", 3) >= 3 and getattr(t, "missed", 0) == 0]
        for tid in list(self.h_hist):
            if tid not in {t.id for t in confirmed}:
                self.h_hist.pop(tid, None)
        if not confirmed:
            self._reset_engagement()
            return d

        close = [t for t in confirmed if t.box.h / H >= SWARM_H]
        self.stats["max_close_zombies"] = max(self.stats["max_close_zombies"], len(close))
        swarmed = len(close) >= SWARM_N

        best, best_score = None, -1e9
        for t in confirmed:
            h = t.box.h / H
            aimability = 1.0 - min(1.0, abs(t.box.cx - W / 2) / (W / 2))
            score = 3.0 * h + 2.0 * self._approach(t.id, h, now) + 0.5 * aimability
            if t.id == self.cur_id:
                score += 0.3      # stickiness: don't flip between similar threats
            if swarmed:
                score = h         # swarmed: nearest first, nothing else matters
            if score > best_score:
                best, best_score = t, score
        if best.id != self.cur_id:
            if self.cur_id is not None:
                self.stats["target_switches"] += 1
            self.cur_id = best.id
            self._reset_engagement()

        h = best.box.h / H
        aim_y = S["aim_y_offset"]
        if h >= CLOSE_H:
            aim_y = max(aim_y, 0.35)   # chest: a close head is often above the pitch cap
            self.stats["chest_aim_frames"] += 1
        ax, ay = best.box.cx, best.box.y1 + aim_y * best.box.h
        d.target, d.threat = best, round(best_score, 2)
        d.err_x, d.err_y = ax - W / 2, ay - H / 2
        err = (d.err_x ** 2 + d.err_y ** 2) ** 0.5

        # ------------------------------------------------------------ blocked engagement
        if self.engage_t0 is None:
            self.engage_t0, self.err0 = now, err
        no_progress = now - self.engage_t0 >= NO_PROGRESS_S and err > 0.75 * self.err0 and err > S["fire_radius_px"]
        no_points = (self.fire_t0 is not None and now - self.fire_t0 >= FIRE_NO_POINTS_S
                     and points is not None and self.points_at_fire is not None and points <= self.points_at_fire)
        if (no_progress or no_points) and now >= self.repo_cooldown and now >= self.repo_until:
            others = [t.box.cx for t in confirmed if t.id != best.id]
            if others:  # step toward the side with fewer zombies
                left = sum(1 for x in others if x < W / 2)
                self.repo_key = "a" if left < len(others) - left else "d"
            else:       # alone: alternate sides
                self.repo_key = "d" if self.repo_key == "a" else "a"
            self.repo_until = now + REPOSITION_S
            self.repo_cooldown = now + REPOSITION_S + REPOSITION_COOLDOWN_S
            self.stats["repositions"] += 1
            self.engage_t0, self.err0 = now + REPOSITION_S, err   # re-measure progress after moving
            self.fire_t0 = None

        # ------------------------------------------------------------ movement
        if now < self.repo_until:
            d.keys[self.repo_key] = True
            d.label = "REPOSITION"
        elif swarmed:
            cx = sum(t.box.cx for t in close) / len(close)
            d.keys["s"] = True
            d.keys["a" if cx > W / 2 else "d"] = True   # strafe away from the swarm's centre
            d.label = "SWARMED"
            self.stats["swarm_frames"] += 1
        elif h >= max(CLOSE_H, S["retreat_box_h"] / H):
            others = [t.box.cx for t in confirmed if t.id != best.id]
            side_x = sum(others) / len(others) if others else best.box.cx
            d.keys["s"] = True
            d.keys["a" if side_x > W / 2 else "d"] = True  # kite: back off and away from the others
            d.label = "KITE"
            self.stats["kite_frames"] += 1
        return d

    def on_fire(self, now, points):
        if self.fire_t0 is None:
            self.fire_t0, self.points_at_fire = now, points

    def on_points(self, points):
        """Points went up: the shots are landing, so the engagement is not blocked."""
        self.fire_t0 = None
        self.points_at_fire = points

    def _reset_engagement(self):
        self.engage_t0 = self.err0 = self.fire_t0 = self.points_at_fire = None
