"""MongoDB connection and the names every part of ZombieLoop shares.

Perception (A), evolve (B) and the dashboard (C) import from here so collection
and index names live in exactly one place.
"""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DB_NAME = os.environ.get("MONGODB_DB", "zombieloop")

# Memory layers (CoALA types), one collection each
WORKING_MEMORY = "working_memory"  # working: coach's current state, expires by TTL
HUD_STATES = "hud_states"          # episodic, raw: time series of HUD readings
EPISODES = "episodes"              # episodic, case bank: one doc per round, vector-indexed
PLAYBOOKS = "playbooks"            # procedural: evolved playbooks with lineage
LESSONS = "lessons"                # semantic: lessons with evidence and status
SITUATIONS = "situations"          # taxonomic: situation categories as a tree
EXPERIMENTS = "experiments"        # provenance: run settings and seeds
GENERATIONS = "generations"        # provenance: per-arm, per-generation scores
RUNS = "runs"                      # episodic: one real-game evaluation of a playbook
JOBS = "jobs"                      # evaluation queue; workers claim atomically
LLM_CALLS = "llm_calls"            # provenance: every Luminal attempt (model, latency, tokens)

EPISODES_VEC_INDEX = "episodes_vec"
LESSONS_VEC_INDEX = "lessons_vec"
STATE_DIMS = 12          # see memory.state_vector()
LESSON_EMBED_DIMS = 384  # all-MiniLM-L6-v2, run locally

LESSON_STATUSES = ("candidate", "validated", "retired")


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    return MongoClient(os.environ["MONGODB_URI"], appname="zombieloop")


def get_db():
    return get_client()[DB_NAME]
