"""Memory reads and writes for Gulag Loop.

Design and the papers behind it: "Memory design and evidence" tab of the plan doc.
- retrieve(): Generative Agents score (relevance + recency + importance, each
  min-max scaled, equal weights) computed in one aggregation.
- update_lessons_after_scoring(): validated memory writes (Xiong et al. 2025),
  done in one transaction with the playbook's score.
"""
from datetime import datetime, timezone

from .db import (
    EPISODES, EPISODES_VEC_INDEX, HUD_STATES, LESSONS, LESSONS_VEC_INDEX,
    PLAYBOOKS, SITUATIONS, WORKING_MEMORY, get_client, get_db,
)

PERKS = ("jugg", "speed", "doubletap", "quickrevive")
RECENCY_DECAY_PER_MINUTE = 0.995


def _now():
    return datetime.now(timezone.utc)


def _clip(x):
    return max(0.0, min(1.0, float(x)))


# ---------------------------------------------------------------- state vector

def state_vector(state: dict) -> list[float]:
    """The 12 numbers from the shared contract, each scaled to roughly 0..1."""
    perks = set(state.get("perks", []))
    return [
        _clip(state.get("round", 0) / 30),
        _clip(state.get("points", 0) / 10000),
        _clip(state.get("health", 1.0)),
        _clip(state.get("weapon_tier", 0) / 3),
        *(1.0 if p in perks else 0.0 for p in PERKS),
        _clip(state.get("doors", 0) / 6),
        _clip(state.get("box_uses", 0) / 10),
        1.0 if state.get("downed") else 0.0,
        1.0 if state.get("pap") else 0.0,
    ]


def importance(round_no: int, downs: int, bought_upgrade: bool) -> int:
    """Deterministic 1-10 importance, so retrieval needs no LLM call."""
    if downs > 0:
        return 10
    if bought_upgrade:
        return 7
    return 4 if round_no >= 10 else 2


# ---------------------------------------------------------------- episodic

def record_hud_state(state: dict, run_id: str) -> None:
    """A writes one HUD reading (every ~2 s) into the time series collection."""
    doc = {**state, "ts": state.get("ts") or _now(), "meta": {"run_id": run_id}}
    doc.setdefault("vec", state_vector(state))
    get_db()[HUD_STATES].insert_one(doc)


def record_episode(episode: dict) -> None:
    """B's simulator writes one case per round: state at round start, playbook, outcome."""
    episode = {**episode}
    episode.setdefault("ts", _now())
    get_db()[EPISODES].insert_one(episode)


def summarize_round(run_id: str, round_no: int, playbook_id: str | None = None,
                    situation: str | None = None) -> None:
    """Roll one live round of hud_states up into an episode ($group + $merge)."""
    get_db()[HUD_STATES].aggregate([
        {"$match": {"meta.run_id": run_id, "round": round_no}},
        {"$sort": {"ts": 1}},
        {"$group": {
            "_id": None,
            "first": {"$first": "$$ROOT"},
            "last": {"$last": "$$ROOT"},
            "downed_any": {"$max": {"$cond": ["$downed", 1, 0]}},
        }},
        {"$project": {
            "_id": f"{run_id}-r{round_no}",
            "source": "live", "arm": "live", "run_id": run_id, "round": round_no,
            "vec": "$first.vec", "ts": "$last.ts",
            "playbook_id": playbook_id, "situation": situation,
            "outcome": {
                "survived": {"$not": ["$last.downed"]},
                "downs": "$downed_any",
                "points_gained": {"$subtract": ["$last.points", "$first.points"]},
            },
            "bought_upgrade": {"$or": [
                {"$gt": [{"$size": {"$ifNull": ["$last.perks", []]}},
                         {"$size": {"$ifNull": ["$first.perks", []]}}]},
                {"$and": [{"$eq": ["$last.pap", True]}, {"$ne": ["$first.pap", True]}]},
            ]},
        }},
        {"$set": {"importance": {"$switch": {"branches": [
            {"case": {"$gt": ["$outcome.downs", 0]}, "then": 10},
            {"case": "$bought_upgrade", "then": 7},
            {"case": {"$gte": ["$round", 10]}, "then": 4}], "default": 2}}}},
        {"$unset": "bought_upgrade"},
        {"$merge": {"into": EPISODES, "on": "_id", "whenMatched": "replace", "whenNotMatched": "insert"}},
    ])


def _scaled(field: str) -> dict:
    lo, hi = f"${field}_lo", f"${field}_hi"
    return {"$cond": [{"$eq": [hi, lo]}, 0,
                      {"$divide": [{"$subtract": [f"${field}", lo]}, {"$subtract": [hi, lo]}]}]}


def plain_similar(vec: list[float], arm: str = "C", k: int = 5) -> list[dict]:
    """Top-k by vector similarity alone; the dashboard shows this beside retrieve()."""
    return list(get_db()[EPISODES].aggregate([
        {"$vectorSearch": {"index": EPISODES_VEC_INDEX, "path": "vec", "queryVector": vec,
                           "numCandidates": 100, "limit": k, "filter": {"arm": arm}}},
        {"$project": {"_id": 1, "round": 1, "outcome": 1, "situation": 1, "playbook_id": 1,
                      "relevance": {"$meta": "vectorSearchScore"}}},
    ]))


def retrieve(vec: list[float], arm: str = "C", k: int = 5, now: datetime | None = None) -> list[dict]:
    """Generative Agents retrieval: relevance + recency + importance, ranked in MongoDB."""
    now = now or _now()
    return list(get_db()[EPISODES].aggregate([
        {"$vectorSearch": {"index": EPISODES_VEC_INDEX, "path": "vec", "queryVector": vec,
                           "numCandidates": 200, "limit": 50, "filter": {"arm": arm}}},
        {"$addFields": {
            "relevance": {"$meta": "vectorSearchScore"},
            "recency": {"$pow": [RECENCY_DECAY_PER_MINUTE,
                                 {"$divide": [{"$subtract": [now, "$ts"]}, 60000]}]},
        }},
        {"$setWindowFields": {"output": {
            "relevance_lo": {"$min": "$relevance"}, "relevance_hi": {"$max": "$relevance"},
            "recency_lo": {"$min": "$recency"}, "recency_hi": {"$max": "$recency"},
            "importance_lo": {"$min": "$importance"}, "importance_hi": {"$max": "$importance"},
        }}},
        {"$addFields": {"score": {"$add": [_scaled("relevance"), _scaled("recency"), _scaled("importance")]}}},
        {"$sort": {"score": -1}},
        {"$limit": k},
        {"$lookup": {"from": PLAYBOOKS, "localField": "playbook_id", "foreignField": "_id", "as": "playbook"}},
        {"$project": {"_id": 1, "round": 1, "outcome": 1, "situation": 1, "importance": 1,
                      "score": 1, "relevance": 1, "recency": 1, "playbook_id": 1,
                      "playbook_params": {"$first": "$playbook.params"}}},
    ]))


# ---------------------------------------------------------------- taxonomic + semantic

def situation_ancestors(situation: str) -> list[str]:
    """The situation and all its parents, walked with $graphLookup."""
    rows = list(get_db()[SITUATIONS].aggregate([
        {"$match": {"_id": situation}},
        {"$graphLookup": {"from": SITUATIONS, "startWith": "$parent", "connectFromField": "parent",
                          "connectToField": "_id", "as": "ancestors", "depthField": "depth"}},
    ]))
    if not rows:
        return [situation]
    return [situation] + [a["_id"] for a in sorted(rows[0]["ancestors"], key=lambda a: a["depth"])]


def lessons_for(situation: str, query_embedding: list[float] | None, arm: str = "C",
                k: int = 5, statuses=("validated", "candidate")) -> list[dict]:
    """Lessons tagged with the situation or any parent, ranked by text similarity."""
    tags = situation_ancestors(situation)
    filt = {"arm": arm, "status": {"$in": list(statuses)}, "situations": {"$in": tags}}
    if query_embedding is None:
        return list(get_db()[LESSONS].find(filt, {"embedding": 0})
                    .sort("evidence.mean_delta", -1).limit(k))
    return list(get_db()[LESSONS].aggregate([
        {"$vectorSearch": {"index": LESSONS_VEC_INDEX, "path": "embedding",
                           "queryVector": query_embedding, "numCandidates": 100, "limit": k,
                           "filter": filt}},
        {"$project": {"embedding": 0, "similarity": {"$meta": "vectorSearchScore"}}},
    ]))


# ---------------------------------------------------------------- procedural

def playbook_lineage(playbook_id: str) -> list[dict]:
    """The playbook and its ancestors, nearest first."""
    rows = list(get_db()[PLAYBOOKS].aggregate([
        {"$match": {"_id": playbook_id}},
        {"$graphLookup": {"from": PLAYBOOKS, "startWith": "$parent", "connectFromField": "parent",
                          "connectToField": "_id", "as": "ancestors", "depthField": "depth"}},
    ]))
    if not rows:
        return []
    me = {k: v for k, v in rows[0].items() if k != "ancestors"}
    return [me] + sorted(rows[0]["ancestors"], key=lambda a: a["depth"])


def set_best_playbook(arm: str, playbook_id: str) -> None:
    pb = get_db()[PLAYBOOKS]
    pb.update_many({"arm": arm, "is_best": True}, {"$set": {"is_best": False}})
    pb.update_one({"_id": playbook_id}, {"$set": {"is_best": True}})


def update_lessons_after_scoring(playbook_id: str, scores: dict, delta: float) -> None:
    """Validated memory writes: store the playbook's scores and credit its lessons, atomically.

    delta = this playbook's held-out score minus its parent's. Only arm C lessons
    change status; arm B keeps everything, as the experiment requires.
    """
    db = get_db()
    with get_client().start_session() as s, s.start_transaction():
        pb = db[PLAYBOOKS].find_one_and_update({"_id": playbook_id}, {"$set": scores}, session=s)
        if not pb or not pb.get("lesson_ids"):
            return
        db[LESSONS].update_many(
            {"_id": {"$in": pb["lesson_ids"]}, "arm": "C", "status": {"$ne": "retired"}},
            [
                {"$set": {"evidence.deltas": {"$concatArrays": [{"$ifNull": ["$evidence.deltas", []]}, [delta]]}}},
                {"$set": {"evidence.uses": {"$size": "$evidence.deltas"},
                          "evidence.mean_delta": {"$avg": "$evidence.deltas"}}},
                {"$set": {"status": {"$switch": {"branches": [
                    {"case": {"$lt": ["$evidence.uses", 2]}, "then": "$status"},
                    {"case": {"$lt": [{"$max": {"$lastN": {"input": "$evidence.deltas", "n": 2}}}, 0]},
                     "then": "retired"},
                    {"case": {"$gt": ["$evidence.mean_delta", 0]}, "then": "validated"}],
                    "default": "retired"}}}},
                {"$set": {"retired_gen": {"$cond": [
                    {"$and": [{"$eq": ["$status", "retired"]}, {"$eq": [{"$ifNull": ["$retired_gen", None]}, None]}]},
                    pb.get("gen"), {"$ifNull": ["$retired_gen", None]}]}}},
            ],
            session=s,
        )


# ---------------------------------------------------------------- working

def set_working_memory(state: dict, retrieved: list, callout: str | None, ttl_seconds: int = 600) -> None:
    from datetime import timedelta
    get_db()[WORKING_MEMORY].replace_one(
        {"_id": "coach"},
        {"_id": "coach", "state": state, "retrieved": retrieved, "callout": callout,
         "expires_at": _now() + timedelta(seconds=ttl_seconds)},
        upsert=True,
    )
