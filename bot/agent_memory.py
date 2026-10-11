"""MongoDB as the agents' memory, not a logbook. Uses the team's Atlas collections/indexes (scripts/setup_db.py on master):

  episodes    one doc per game: 12-dim diagnostics vector `vec` (index `episodes_vec`, euclidean, filters arm/source),
              situation tag, outcome, what changed vs the parent. Retrieved with $vectorSearch before every proposal:
              "games that looked like the champion's current situation, what was changed, what happened".
  lessons     a promotion writes a candidate lesson; the champion's next re-evaluation validates or retires it, in one
              multi-document transaction with the experiment's champion record. Validated lessons are fed to agents.
  hud_states  time-series collection: every 2 s heartbeat (fps, detections, action, points) for trend aggregations.

The retrieved memories are stored on each proposed strategy (`memory_context`) so the dashboard can show what the
agent was shown.
"""
import math
from datetime import datetime, timezone

ARM = "rishit-kino"  # shared memory across this bot's experiments on the same controller


def _now():
    return datetime.now(timezone.utc)


def _c(x):
    return max(0.0, min(1.0, float(x)))


def diag_vector(d, survival_s=0.0, points=0):
    """12 numbers in 0..1 describing how a game went (matches STATE_DIMS=12 of the episodes_vec index)."""
    fz = d.get("first_zombie_seen_s")
    return [
        _c(d.get("time_no_target_pct", 100) / 100),
        _c(d.get("det_count_avg", 0) / 1.0),
        _c((d.get("hit_rate") or 0) * 5),
        _c(d.get("close_calls", 0) / 150),
        _c(d.get("retreat_frames", 0) / 300),
        _c(survival_s / 180),
        _c(points / 500),
        _c((d.get("kills") or 0) / 5),
        _c(d.get("overshoot_rate", 0) * 10),
        1.0 if d.get("death_unseen_attacker") else 0.0,
        _c(fz / 60) if fz is not None else 1.0,
        _c(d.get("damage_frames", 0) / 150),
    ]


def situation(d):
    """Coarse tag for lessons: what was going wrong in this game."""
    if d.get("death_unseen_attacker"):
        return "ambushed"
    if d.get("time_no_target_pct", 100) > 85:
        return "blind"
    if d.get("close_calls", 0) > 60:
        return "overrun"
    if d.get("shots_fired", 0) > 15 and (d.get("hit_rate") or 0) < 0.05:
        return "missing"
    return "engaged"


class AgentMemory:
    def __init__(self, db, experiment_id, arm=ARM, log=print):
        self.db, self.exp, self.arm, self.log = db, experiment_id, arm, log

    # ---------- episodes (write + $vectorSearch) ----------
    def record_episode(self, job_id, strategy_id, persona, params, parent_params, result, role, parent_mean=None):
        d = result.get("diagnostics", {})
        fit = result["points"] + 5 * result["survival_s"]
        changed = {k: [parent_params.get(k), v] for k, v in params.items()
                   if parent_params is not None and parent_params.get(k) != v}
        doc = {"_id": job_id, "source": "live", "arm": self.arm, "experiment_id": self.exp, "run_id": job_id,
               "ts": _now(), "vec": diag_vector(d, result["survival_s"], result["points"]),
               "playbook_id": strategy_id, "persona": persona, "role": role, "situation": situation(d),
               "params": params, "changed": changed,
               "outcome": {"fitness": round(fit, 1), "points_gained": result["points"],
                           "survival_s": result["survival_s"], "kills": d.get("kills"), "survived": False},
               "delta_vs_parent": round(fit - parent_mean, 1) if parent_mean is not None else None,
               "importance": 10 if (d.get("kills") or 0) >= 2 else (6 if result["points"] > 0 else 2)}
        self.db.episodes.replace_one({"_id": job_id}, doc, upsert=True)
        return doc

    def similar(self, diag, survival_s, points, k=5, exclude_playbook=None):
        """$vectorSearch: past games whose diagnostics look like this one; returns what was changed and how it went."""
        q = diag_vector(diag, survival_s, points)
        pipeline = [
            {"$vectorSearch": {"index": "episodes_vec", "path": "vec", "queryVector": q, "numCandidates": 200,
                               "limit": k * 3, "filter": {"arm": self.arm, "source": "live"}}},
            {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
            {"$match": {"changed": {"$ne": {}}, **({"playbook_id": {"$ne": exclude_playbook}} if exclude_playbook else {})}},
            {"$sort": {"similarity": -1, "importance": -1}},
            {"$limit": k},
            {"$project": {"_id": 1, "persona": 1, "situation": 1, "changed": 1, "outcome": 1, "delta_vs_parent": 1,
                          "similarity": {"$round": ["$similarity", 3]}}},
        ]
        try:
            return list(self.db.episodes.aggregate(pipeline))
        except Exception as e:  # index still building, or no data yet
            self.log(f"[memory] vectorSearch unavailable: {type(e).__name__}")
            return []

    # ---------- lessons (candidate -> validated / retired, transactional) ----------
    def add_lesson(self, strategy_id, persona, changed, sit, mean_new, mean_old, games):
        ch = ", ".join(f"{k} {a}->{b}" for k, (a, b) in changed.items()) or "no parameter change"
        doc = {"arm": self.arm, "experiment_id": self.exp, "status": "candidate", "planted": False,
               "situations": [sit], "strategy_id": strategy_id, "persona": persona, "changed": changed,
               "text": (f"In '{sit}' games, {persona} changed {ch}; 2-game mean fitness {mean_new:.0f} vs champion "
                        f"{mean_old:.0f} ({(mean_new / max(mean_old, 1) - 1) * 100:+.0f}%)."),
               "evidence": {"mean_new": round(mean_new, 1), "mean_old": round(mean_old, 1), "games": games},
               "created_at": _now()}
        lid = self.db.lessons.insert_one(doc).inserted_id
        self.log(f"[memory] lesson candidate: {doc['text']}")
        return lid

    def judge_lessons(self, champion_id, champion_mean, baseline_mean):
        """After the champion is re-evaluated: validate its candidate lessons if it still beats the old champion,
        else retire them. Lesson status + experiment record change together in one transaction."""
        cands = list(self.db.lessons.find({"arm": self.arm, "strategy_id": champion_id, "status": "candidate"}))
        if not cands:
            return
        status = "validated" if champion_mean > cands[0]["evidence"]["mean_old"] else "retired"

        def txn(s):
            self.db.lessons.update_many({"_id": {"$in": [c["_id"] for c in cands]}},
                                        {"$set": {"status": status, "judged_at": _now(),
                                                  "evidence.mean_after_reeval": round(champion_mean, 1)}}, session=s)
            self.db.experiments.update_one({"_id": self.exp},
                                           {"$inc": {f"lessons_{status}": len(cands)},
                                            "$set": {"last_lesson_judged_at": _now()}}, session=s)
        with self.db.client.start_session() as s:
            s.with_transaction(txn)
        self.log(f"[memory] {len(cands)} lesson(s) {status} (champion re-eval mean {champion_mean:.0f})")

    def lessons_for(self, sit, k=3):
        return list(self.db.lessons.find({"arm": self.arm, "status": {"$in": ["validated", "candidate"]},
                                          "situations": sit},
                                         {"_id": 1, "text": 1, "status": 1})
                    .sort([("status", -1), ("evidence.mean_new", -1)]).limit(k))

    # ---------- context for the agent ----------
    def context_for(self, champion_id, diag, survival_s, points):
        sit = situation(diag)
        sims = self.similar(diag, survival_s, points, exclude_playbook=champion_id)
        less = self.lessons_for(sit)
        ctx = {"memory": "retrieved from MongoDB Atlas for this proposal",
               "current_situation": sit,
               "similar_past_games": [{"situation": e.get("situation"), "persona": e.get("persona"),
                                       "changed": e.get("changed"), "fitness": e["outcome"]["fitness"],
                                       "kills": e["outcome"].get("kills"), "delta_vs_parent": e.get("delta_vs_parent"),
                                       "similarity": e.get("similarity")} for e in sims],
               "lessons": [{"status": l["status"], "text": l["text"]} for l in less]}
        ref = {"situation": sit, "similar_episode_ids": [e["_id"] for e in sims],
               "similarities": [e.get("similarity") for e in sims], "lesson_ids": [str(l["_id"]) for l in less],
               "lesson_texts": [l["text"] for l in less]}
        return ctx, ref

    # ---------- time series ----------
    def hud_point(self, job_id, fps, detections, action, points, elapsed_s):
        try:
            self.db.hud_states.insert_one({"ts": _now(), "meta": {"run_id": job_id, "experiment_id": self.exp,
                                                                  "arm": self.arm},
                                           "fps": fps, "detections": detections, "action": action,
                                           "points": points, "elapsed_s": elapsed_s})
        except Exception:
            pass

    def run_trend(self, job_id):
        """$setWindowFields over the time series: 10 s moving average of detections and points for one game."""
        return list(self.db.hud_states.aggregate([
            {"$match": {"meta.run_id": job_id}},
            {"$setWindowFields": {"sortBy": {"ts": 1}, "output": {
                "det_ma": {"$avg": "$detections", "window": {"range": [-10, 0], "unit": "second"}},
                "points_ma": {"$avg": "$points", "window": {"range": [-10, 0], "unit": "second"}}}}},
            {"$project": {"_id": 0, "elapsed_s": 1, "det_ma": {"$round": ["$det_ma", 2]}, "points_ma": 1}}]))

    def backfill(self, steady_state=None, evo_states=()):
        """Seed the memory with earlier games (same controller) so the first proposals already have context."""
        n = 0
        for path in evo_states:
            import json
            s = json.load(open(path))
            strat = {c["_id"]: c for c in s["strategies"]}
            means = {}
            for r in s["runs"]:
                means.setdefault(r["strategy_id"], []).append(r["points"] + 5 * r["survival_s"])
            for r in s["runs"]:
                c = strat.get(r["strategy_id"], {})
                parent = strat.get((c.get("parent_ids") or [None])[0], {})
                pm = sum(means.get(parent.get("_id"), [0])) / max(1, len(means.get(parent.get("_id"), [])))
                self.record_episode("mem-" + r["job_id"], r["strategy_id"], c.get("persona"), c.get("params", {}),
                                    parent.get("params") if parent else None, r, "backfill",
                                    pm if parent else None)
                n += 1
        if steady_state:
            s = steady_state
            for r in s["runs"]:
                c = s["strategies"].get(r["strategy_id"], {})
                parent = s["strategies"].get((c.get("parent_ids") or [None])[0], {})
                self.record_episode("mem-" + r["job_id"], r["strategy_id"], c.get("persona"), c.get("params", {}),
                                    parent.get("params") if parent else None, r, r.get("role", "backfill"), None)
                n += 1
        self.log(f"[memory] backfilled {n} episodes into arm '{self.arm}'")
        return n
