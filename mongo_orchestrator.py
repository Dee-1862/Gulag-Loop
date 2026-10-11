"""MongoDB storage for the Luminal evolution loop.

Replaces the JSON state file used by evolve_local.py. Ranking, generation and job
rules are the same functions from evolution.py; this module only loads that state
from Atlas, persists what advance() adds, and gives workers an atomic job queue.

    python mongo_orchestrator.py --experiment exp-kino-1 init --map kino
    python mongo_orchestrator.py --experiment exp-kino-1 claim --worker laptop-1
    python mongo_orchestrator.py --experiment exp-kino-1 complete --result result.json
    python mongo_orchestrator.py --experiment exp-kino-1 propose [--dry-run]
    python mongo_orchestrator.py --experiment exp-kino-1 status
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pymongo import ReturnDocument

from common.db import EXPERIMENTS, GENERATIONS, JOBS, LLM_CALLS, PLAYBOOKS, RUNS, get_client, get_db
from evolution import RunResult, advance, make_job, next_request, ranked_parents
from strategy import BASELINE, Proposal, Strategy

ARM = "bot"  # playbooks from the real-game bot; the simulator experiment uses arms A/B/C


def _now():
    return datetime.now(timezone.utc)


def _as_datetime(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


class MongoOrchestrator:
    def __init__(self, experiment_id, db=None):
        self.experiment_id = experiment_id
        self.db = db if db is not None else get_db()

    # ------------------------------------------------------------ setup

    def baseline_id(self):
        return f"{self.experiment_id}:baseline"

    def init_experiment(self, map_name, notes=None):
        """Create the experiment, the baseline playbook and two baseline jobs."""
        if self.db[EXPERIMENTS].find_one({"_id": self.experiment_id}):
            raise ValueError(f"Experiment {self.experiment_id} already exists")
        now = _now()
        with get_client().start_session() as s, s.start_transaction():
            self.db[EXPERIMENTS].insert_one({
                "_id": self.experiment_id, "design": "bot", "map": map_name, "generation": 0,
                "notes": notes, "started_at": now}, session=s)
            self.db[PLAYBOOKS].insert_one({
                "_id": self.baseline_id(), "experiment_id": self.experiment_id, "arm": ARM,
                "gen": 0, "generation": 0, "persona": "Baseline", "model": None,
                "parent": None, "parent_ids": [], "params": BASELINE.model_dump(),
                "rationale": "Hand-written baseline.", "fallback": False,
                "is_best": False, "lesson_ids": [], "created_at": now}, session=s)
            self.db[JOBS].insert_many(
                [self._job_doc(make_job(self.baseline_id(), 0, "baseline")) for _ in range(2)], session=s)

    def _job_doc(self, job):
        return {**job, "_id": job["job_id"], "experiment_id": self.experiment_id, "created_at": _now()}

    # ------------------------------------------------------------ state for evolution.py

    def load_state(self):
        """Rebuild the dict evolution.py expects, from MongoDB."""
        exp = self.db[EXPERIMENTS].find_one({"_id": self.experiment_id})
        if not exp:
            raise ValueError(f"Unknown experiment {self.experiment_id}")
        q = {"experiment_id": self.experiment_id}
        strategies = list(self.db[PLAYBOOKS].find(q, {"_id": 1, "params": 1, "persona": 1, "rationale": 1,
                                                       "fallback": 1, "generation": 1}))
        runs = list(self.db[RUNS].find(q, {"_id": 0, "job_id": 1, "strategy_id": 1, "fitness": 1,
                                           "diagnostics": 1}).sort("completed_at", 1))
        jobs = list(self.db[JOBS].find(q, {"_id": 0, "job_id": 1, "strategy_id": 1, "generation": 1,
                                           "kind": 1, "status": 1}))
        history = [{k: g[k] for k in ("generation", "champion_id", "best_mean_fitness", "evaluation_count")}
                   for g in self.db[GENERATIONS].find({**q, "arm": ARM}).sort("gen", 1)]
        environment = {"worker": None, "map": exp["map"]}
        return {"version": 1, "generation": exp["generation"], "environment": environment,
                "strategies": strategies, "runs": runs, "llm_calls": [], "history": history, "jobs": jobs}

    # ------------------------------------------------------------ worker side

    def claim_job(self, worker):
        """Atomically hand the oldest queued job to one worker, with controller-ready params."""
        job = self.db[JOBS].find_one_and_update(
            {"experiment_id": self.experiment_id, "status": "queued"},
            {"$set": {"status": "running", "worker": worker, "claimed_at": _now()}},
            sort=[("created_at", 1)], return_document=ReturnDocument.AFTER)
        if not job:
            return None
        params = self.db[PLAYBOOKS].find_one({"_id": job["strategy_id"]}, {"params": 1})["params"]
        return {"job_id": job["job_id"], "strategy_id": job["strategy_id"], "generation": job["generation"],
                "kind": job["kind"], "params": params}

    def complete_job(self, result):
        """Store one run result and close its job, in one transaction. Exact replays are no-ops."""
        result = RunResult.model_validate(result)
        doc = result.model_dump()
        exp = self.db[EXPERIMENTS].find_one({"_id": self.experiment_id})
        if result.map != exp["map"]:
            raise ValueError(f"Experiment {self.experiment_id} runs on map {exp['map']!r}, got {result.map!r}")
        with get_client().start_session() as s, s.start_transaction():
            job = self.db[JOBS].find_one({"_id": result.job_id, "experiment_id": self.experiment_id}, session=s)
            if job is None or job["strategy_id"] != result.strategy_id:
                raise ValueError("Result must match an issued job and strategy ID")
            existing = self.db[RUNS].find_one({"_id": result.job_id}, session=s)
            if existing:
                if {k: existing[k] for k in doc} != doc:
                    raise ValueError("Conflicting result for an already completed job")
                return False
            self.db[RUNS].insert_one({**doc, "_id": result.job_id, "experiment_id": self.experiment_id,
                                      "generation": job["generation"], "kind": job["kind"],
                                      "fitness": result.fitness, "completed_at": _now()}, session=s)
            self.db[JOBS].update_one({"_id": result.job_id},
                                     {"$set": {"status": "done", "completed_at": _now()}}, session=s)
        return True

    # ------------------------------------------------------------ orchestrator side

    def proposal_request(self):
        return next_request(self.load_state())

    def propose(self, client):
        """Run one generation through Luminal, then persist everything it added atomically."""
        state = self.load_state()
        known = {s["_id"] for s in state["strategies"]}
        known_jobs = {j["job_id"] for j in state["jobs"]}
        updated = advance(state, client)  # model calls happen here, outside the transaction
        children = [c for c in updated["strategies"] if c["_id"] not in known]
        new_jobs = [j for j in updated["jobs"] if j["job_id"] not in known_jobs]
        history = updated["history"][-1]
        calls = updated["llm_calls"]
        with get_client().start_session() as s, s.start_transaction():
            # Optimistic lock: refuse if another proposer advanced this experiment meanwhile.
            moved = self.db[EXPERIMENTS].update_one(
                {"_id": self.experiment_id, "generation": state["generation"]},
                {"$set": {"generation": updated["generation"]}}, session=s)
            if moved.matched_count != 1:
                raise RuntimeError("Experiment advanced concurrently; nothing was saved")
            self.db[PLAYBOOKS].insert_many([{
                **c, "experiment_id": self.experiment_id, "arm": ARM, "gen": c["generation"],
                "parent": (c.get("parent_ids") or [None])[0], "is_best": False, "lesson_ids": [],
                "created_at": _as_datetime(c.get("created_at")) or _now()} for c in children], session=s)
            if calls:
                self.db[LLM_CALLS].insert_many([{
                    **c, "experiment_id": self.experiment_id,
                    "created_at": _as_datetime(c.get("created_at"))} for c in calls], session=s)
            self.db[JOBS].insert_many([self._job_doc(j) for j in new_jobs], session=s)
            self.db[GENERATIONS].insert_one({
                **history, "experiment_id": self.experiment_id, "arm": ARM, "gen": history["generation"],
                "recorded_at": _now()}, session=s)
            self.db[PLAYBOOKS].update_many({"experiment_id": self.experiment_id, "is_best": True},
                                           {"$set": {"is_best": False}}, session=s)
            self.db[PLAYBOOKS].update_one({"_id": history["champion_id"]}, {"$set": {"is_best": True}}, session=s)
        return {"generation": updated["generation"], "children": children, "llm_calls": calls,
                "jobs": new_jobs, "champion": history}

    def leaderboard(self, limit=5):
        """Mean fitness per playbook, computed by MongoDB (the dashboard reads this)."""
        return list(self.db[RUNS].aggregate([
            {"$match": {"experiment_id": self.experiment_id}},
            {"$group": {"_id": "$strategy_id", "mean_fitness": {"$avg": "$fitness"},
                        "runs": {"$sum": 1}, "best": {"$max": "$fitness"}}},
            {"$sort": {"mean_fitness": -1, "_id": 1}},
            {"$limit": limit},
            {"$lookup": {"from": PLAYBOOKS, "localField": "_id", "foreignField": "_id", "as": "pb"}},
            {"$project": {"strategy_id": "$_id", "_id": 0, "mean_fitness": 1, "runs": 1, "best": 1,
                          "persona": {"$first": "$pb.persona"}, "generation": {"$first": "$pb.generation"},
                          "fallback": {"$first": "$pb.fallback"}}},
        ]))

    def status(self):
        state = self.load_state()
        counts = {r["_id"]: r["n"] for r in self.db[JOBS].aggregate([
            {"$match": {"experiment_id": self.experiment_id}},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
        return {"experiment": self.experiment_id, "generation": state["generation"], "jobs": counts,
                "top_parents": [{k: p[k] for k in ("strategy_id", "mean_fitness", "evaluation_count")}
                                for p in ranked_parents(state)],
                "llm_calls": self.db[LLM_CALLS].count_documents({"experiment_id": self.experiment_id})}

    def rehydrate_cache(self, client):
        """Same as evolve_local.py: reuse earlier successful proposals as per-persona fallbacks."""
        for child in self.db[PLAYBOOKS].find({"experiment_id": self.experiment_id, "fallback": False,
                                              "persona": {"$in": ["Bold", "Tuner", "Defender", "Explorer"]}}
                                             ).sort("generation", 1):
            client.cache[child["persona"]] = Proposal(strategy=Strategy.model_validate(child["params"]),
                                                      rationale=child["rationale"])

    def delete_experiment(self):
        q = {"experiment_id": self.experiment_id}
        for coll in (PLAYBOOKS, JOBS, RUNS, LLM_CALLS, GENERATIONS):
            self.db[coll].delete_many(q)
        self.db[EXPERIMENTS].delete_one({"_id": self.experiment_id})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--experiment", required=True)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--map", required=True)
    claim = sub.add_parser("claim")
    claim.add_argument("--worker", required=True)
    complete = sub.add_parser("complete")
    complete.add_argument("--result", required=True, type=Path)
    propose = sub.add_parser("propose")
    propose.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    args = p.parse_args()

    orch = MongoOrchestrator(args.experiment)
    if args.command == "init":
        orch.init_experiment(args.map)
        print(f"Experiment {args.experiment} created with two baseline jobs.")
    elif args.command == "claim":
        print(json.dumps(orch.claim_job(args.worker), indent=2, default=str))
    elif args.command == "complete":
        docs = json.loads(args.result.read_text())
        for doc in docs if isinstance(docs, list) else [docs]:
            print("stored" if orch.complete_job(doc) else "already stored", doc["job_id"])
    elif args.command == "propose":
        if args.dry_run:
            print(orch.proposal_request().model_dump_json(indent=2))
            return
        from luminal_client import LuminalClient
        client = LuminalClient()
        orch.rehydrate_cache(client)
        out = orch.propose(client)
        print(f"Generation {out['generation']} saved: {len(out['children'])} children, "
              f"{len(out['jobs'])} jobs queued, {len(out['llm_calls'])} Luminal calls logged.")
    elif args.command == "status":
        print(json.dumps(orch.status(), indent=2, default=str))


if __name__ == "__main__":
    main()
