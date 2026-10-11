"""Exercise every memory function against the real cluster, then clean up.

    python scripts/smoke_test.py

All test documents use ids or arms starting with "smoke" and are deleted at the end.
"""
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.errors import WriteError

from common import memory
from common.db import EPISODES, HUD_STATES, LESSON_EMBED_DIMS, LESSONS, PLAYBOOKS, WORKING_MEMORY, get_db

random.seed(7)
db = get_db()
now = datetime.now(timezone.utc)
passed = 0


def check(name, ok, detail=""):
    global passed
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        raise SystemExit(1)
    passed += 1


def wait_for(fn, want, timeout=90):
    """Vector indexes sync a few seconds after writes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = fn()
        if len(out) >= want:
            return out
        time.sleep(3)
    return fn()


def random_state():
    return {"round": random.randint(1, 20), "points": random.randint(0, 9000),
            "health": random.random(), "weapon_tier": random.randint(0, 3),
            "perks": random.sample(memory.PERKS, random.randint(0, 3)),
            "doors": random.randint(0, 6), "box_uses": random.randint(0, 5),
            "downed": random.random() < 0.15, "pap": random.random() < 0.2}


def cleanup():
    db[EPISODES].delete_many({"arm": "smoke"})
    db[EPISODES].delete_many({"run_id": "smoke-run"})
    db[HUD_STATES].delete_many({"meta.run_id": "smoke-run"})
    db[LESSONS].delete_many({"text": {"$regex": "^smoke:"}})
    db[PLAYBOOKS].delete_many({"_id": {"$regex": "^smoke-"}})
    db[WORKING_MEMORY].delete_many({"_id": "coach"})


cleanup()
try:
    # Episodic case bank + Generative Agents retrieval
    for i in range(40):
        s = random_state()
        downs = 1 if s["downed"] else 0
        memory.record_episode({
            "source": "sim", "arm": "smoke", "round": s["round"], "vec": memory.state_vector(s),
            "playbook_id": None, "situation": "box-decision",
            "outcome": {"survived": not s["downed"], "downs": downs, "points_gained": random.randint(0, 2000)},
            "importance": memory.importance(s["round"], downs, s["pap"]),
            "ts": now - timedelta(minutes=random.randint(0, 120)),
        })
    q = memory.state_vector(random_state())
    plain = wait_for(lambda: memory.plain_similar(q, arm="smoke", k=5), 5)
    check("vector search returns 5 similar episodes", len(plain) == 5)
    scored = memory.retrieve(q, arm="smoke", k=5)
    check("retrieve() ranks with relevance + recency + importance", len(scored) == 5
          and all(0 <= r["score"] <= 3 for r in scored)
          and scored == sorted(scored, key=lambda r: -r["score"]),
          f"top score {scored[0]['score']:.2f}, importance {scored[0]['importance']}")
    overlap = len({r["_id"] for r in plain} & {r["_id"] for r in scored})
    print(f"     plain vs scored top-5 overlap: {overlap}/5 (the dashboard shows both)")

    # Raw time series -> round summary via $merge
    for j, (pts, perks) in enumerate([(500, []), (900, []), (1400, ["jugg"]), (1800, ["jugg"])]):
        memory.record_hud_state({"round": 3, "points": pts, "health": 1.0, "weapon_tier": 1,
                                 "perks": perks, "downed": False,
                                 "ts": now + timedelta(seconds=2 * j)}, run_id="smoke-run")
    memory.summarize_round("smoke-run", 3, situation="perk-decision")
    ep = db[EPISODES].find_one({"_id": "smoke-run-r3"})
    check("time series round summary merged into episodes",
          ep is not None and ep["outcome"]["points_gained"] == 1300 and ep["importance"] == 7,
          f"points_gained={ep and ep['outcome']['points_gained']}, importance={ep and ep['importance']}")

    # Taxonomic
    check("situation taxonomy via $graphLookup",
          memory.situation_ancestors("box-decision") == ["box-decision", "mid-game", "game"])

    # Semantic + schema validation
    def emb():
        return [random.gauss(0, 1) for _ in range(LESSON_EMBED_DIMS)]
    good, bad, other = emb(), emb(), emb()
    lessons = [
        {"_id": "smoke-L-good", "arm": "C", "text": "smoke: buy Jugg before the box", "embedding": good,
         "situations": ["box-decision"], "status": "candidate", "planted": False,
         "evidence": {"deltas": [], "uses": 0, "mean_delta": 0}},
        {"_id": "smoke-L-bad", "arm": "C", "text": "smoke: never buy Jugg", "embedding": bad,
         "situations": ["perk-decision"], "status": "candidate", "planted": True,
         "evidence": {"deltas": [], "uses": 0, "mean_delta": 0}},
        {"_id": "smoke-L-other", "arm": "C", "text": "smoke: train in the theater", "embedding": other,
         "situations": ["training-route"], "status": "candidate", "planted": False,
         "evidence": {"deltas": [], "uses": 0, "mean_delta": 0}},
    ]
    db[LESSONS].insert_many(lessons)
    try:
        db[LESSONS].insert_one({"arm": "C", "text": "smoke: invalid", "status": "maybe"})
        check("schema validation rejects an invalid status", False)
    except WriteError:
        check("schema validation rejects an invalid status", True)
    found = wait_for(lambda: memory.lessons_for("box-decision", good, arm="C"), 1)
    check("lessons_for() finds lessons by situation + text similarity",
          [f["_id"] for f in found] == ["smoke-L-good"], f"{[f['_id'] for f in found]}")

    # Procedural lineage + validated memory writes in a transaction
    db[PLAYBOOKS].insert_many([
        {"_id": "smoke-0", "arm": "C", "gen": 0, "parent": None, "params": {"jugg_by_round": 6}, "lesson_ids": []},
        {"_id": "smoke-1", "arm": "C", "gen": 1, "parent": "smoke-0", "params": {"jugg_by_round": 5},
         "lesson_ids": ["smoke-L-good", "smoke-L-bad"]},
        {"_id": "smoke-2", "arm": "C", "gen": 2, "parent": "smoke-1", "params": {"jugg_by_round": 4},
         "lesson_ids": ["smoke-L-good"]},
        {"_id": "smoke-3", "arm": "C", "gen": 2, "parent": "smoke-1", "params": {"jugg_by_round": 9},
         "lesson_ids": ["smoke-L-bad"]},
    ])
    check("playbook lineage via $graphLookup",
          [p["_id"] for p in memory.playbook_lineage("smoke-2")] == ["smoke-2", "smoke-1", "smoke-0"])
    memory.update_lessons_after_scoring("smoke-1", {"heldout_score": 10.0}, delta=-0.2)
    memory.update_lessons_after_scoring("smoke-2", {"heldout_score": 11.5}, delta=+1.5)
    memory.update_lessons_after_scoring("smoke-3", {"heldout_score": 9.0}, delta=-1.0)
    g = db[LESSONS].find_one({"_id": "smoke-L-good"})
    b = db[LESSONS].find_one({"_id": "smoke-L-bad"})
    check("lesson with mean gain > 0 becomes validated", g["status"] == "validated",
          f"deltas={g['evidence']['deltas']}")
    check("planted bad lesson is retired", b["status"] == "retired" and b["retired_gen"] == 2,
          f"deltas={b['evidence']['deltas']}, retired_gen={b.get('retired_gen')}")
    check("playbook score written in the same transaction",
          db[PLAYBOOKS].find_one({"_id": "smoke-2"})["heldout_score"] == 11.5)
    memory.set_best_playbook("C", "smoke-2")
    check("exactly one best playbook", db[PLAYBOOKS].count_documents({"_id": {"$regex": "^smoke-"}, "is_best": True}) == 1)

    # Working memory with TTL
    memory.set_working_memory({"round": 3}, scored[:2], "Buy Jugg now")
    check("working memory written with expiry", db[WORKING_MEMORY].find_one({"_id": "coach"})["callout"] == "Buy Jugg now")
finally:
    cleanup()

print(f"\nall {passed} checks passed, test data cleaned up")
