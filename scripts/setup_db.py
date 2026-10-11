"""Create every ZombieLoop collection and index. Safe to run more than once.

    python scripts/setup_db.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import ASCENDING
from pymongo.errors import CollectionInvalid
from pymongo.operations import SearchIndexModel

from common.db import (
    EPISODES, EPISODES_VEC_INDEX, EXPERIMENTS, GENERATIONS, HUD_STATES, LESSON_EMBED_DIMS,
    LESSON_STATUSES, LESSONS, LESSONS_VEC_INDEX, PLAYBOOKS, SITUATIONS, STATE_DIMS,
    WORKING_MEMORY, get_db,
)

LESSON_SCHEMA = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["arm", "text", "status"],
    "properties": {
        "arm": {"bsonType": "string"},
        "text": {"bsonType": "string"},
        "status": {"enum": list(LESSON_STATUSES)},
        "planted": {"bsonType": "bool"},
        "situations": {"bsonType": "array", "items": {"bsonType": "string"}},
    },
}}

# Taxonomic memory: situation categories, linked to their parent
SITUATIONS_SEED = [
    ("game", None, "Any moment of a game"),
    ("early-game", "game", "Rounds 1 to 5"),
    ("first-rooms", "early-game", "Before any door is opened"),
    ("door-decision", "early-game", "Deciding whether to open a door"),
    ("mid-game", "game", "Rounds 6 to 14"),
    ("perk-decision", "mid-game", "Deciding which perk to buy"),
    ("box-decision", "mid-game", "Deciding whether to use the mystery box"),
    ("training-route", "mid-game", "Choosing a route to circle zombies"),
    ("late-game", "game", "Round 15 and later"),
    ("pack-a-punch", "late-game", "Getting to and using Pack-a-Punch"),
    ("danger", "game", "Something is going wrong"),
    ("low-health", "danger", "Health is low"),
    ("downed", "danger", "Player is down"),
]

VECTOR_INDEXES = {
    EPISODES: SearchIndexModel(name=EPISODES_VEC_INDEX, type="vectorSearch", definition={"fields": [
        {"type": "vector", "path": "vec", "numDimensions": STATE_DIMS, "similarity": "euclidean"},
        {"type": "filter", "path": "arm"},
        {"type": "filter", "path": "source"},
    ]}),
    LESSONS: SearchIndexModel(name=LESSONS_VEC_INDEX, type="vectorSearch", definition={"fields": [
        {"type": "vector", "path": "embedding", "numDimensions": LESSON_EMBED_DIMS, "similarity": "cosine"},
        {"type": "filter", "path": "arm"},
        {"type": "filter", "path": "status"},
        {"type": "filter", "path": "situations"},
    ]}),
}


def create_collections(db):
    existing = set(db.list_collection_names())
    if HUD_STATES not in existing:
        db.create_collection(HUD_STATES, timeseries={
            "timeField": "ts", "metaField": "meta", "granularity": "seconds"})
        print(f"created {HUD_STATES} (time series)")
    if LESSONS not in existing:
        db.create_collection(LESSONS, validator=LESSON_SCHEMA)
        print(f"created {LESSONS} (with schema validation)")
    else:
        db.command("collMod", LESSONS, validator=LESSON_SCHEMA)
    for name in (EPISODES, PLAYBOOKS, SITUATIONS, EXPERIMENTS, GENERATIONS, WORKING_MEMORY):
        try:
            db.create_collection(name)
            print(f"created {name}")
        except CollectionInvalid:
            pass


def create_indexes(db):
    db[PLAYBOOKS].create_index([("arm", ASCENDING), ("gen", ASCENDING)])
    db[PLAYBOOKS].create_index([("arm", ASCENDING), ("is_best", ASCENDING)])
    db[GENERATIONS].create_index(
        [("experiment_id", ASCENDING), ("arm", ASCENDING), ("gen", ASCENDING)], unique=True)
    db[EPISODES].create_index([("arm", ASCENDING), ("ts", ASCENDING)])
    db[LESSONS].create_index([("arm", ASCENDING), ("status", ASCENDING)])
    db[WORKING_MEMORY].create_index("expires_at", expireAfterSeconds=0)
    print("regular and TTL indexes ok")


def create_vector_indexes(db, timeout_s=180):
    for coll, model in VECTOR_INDEXES.items():
        names = {i["name"] for i in db[coll].list_search_indexes()}
        if model.document["name"] not in names:
            db[coll].create_search_index(model)
            print(f"creating vector index {model.document['name']} on {coll}")
    deadline = time.time() + timeout_s
    pending = set(VECTOR_INDEXES)
    while pending and time.time() < deadline:
        for coll in list(pending):
            idx = next(iter(db[coll].list_search_indexes(VECTOR_INDEXES[coll].document["name"])), None)
            if idx and idx.get("queryable"):
                print(f"vector index on {coll} is queryable")
                pending.discard(coll)
        if pending:
            time.sleep(5)
    if pending:
        print(f"still building: {sorted(pending)} (fine, they finish in the background)")


def seed_situations(db):
    for _id, parent, name in SITUATIONS_SEED:
        db[SITUATIONS].replace_one({"_id": _id}, {"_id": _id, "parent": parent, "name": name}, upsert=True)
    print(f"situations seeded: {len(SITUATIONS_SEED)}")


if __name__ == "__main__":
    db = get_db()
    create_collections(db)
    create_indexes(db)
    seed_situations(db)
    create_vector_indexes(db)
    print("setup done")
