"""End-to-end check of the Luminal evolution loop on MongoDB.

    python scripts/test_mongo_luminal.py           # mock agents, no Luminal calls
    python scripts/test_mongo_luminal.py --live    # generation 1 calls Luminal (billable, 4+ requests)
    add --keep to leave the test experiment in the database for the dashboard

Run results are SYNTHETIC (fixed numbers), so nothing here is evidence of improvement.
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common import memory
from common.db import GENERATIONS, JOBS, LLM_CALLS, PLAYBOOKS, RUNS, get_db
from luminal_client import mutate
from mongo_orchestrator import MongoOrchestrator

passed = 0


def check(name, ok, detail=""):
    global passed
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        raise SystemExit(1)
    passed += 1


class MockClient:
    """Same shape as the teammate's offline client: local mutations, no API calls."""
    concurrency = 4

    def generate(self, parent, persona, parent_id, generation, **kwargs):
        return dict(strategy=dict(_id=str(uuid4()), params=mutate(parent, persona).model_dump(),
                                  generation=generation, persona=persona, model=None, parent_ids=[parent_id],
                                  rationale="Offline mock; not Luminal.", fallback=True,
                                  fallback_source="local_mutation"), llm_calls=[])


class RacingClient(MockClient):
    """Advances the experiment behind the orchestrator's back to test the optimistic lock."""
    def __init__(self, orch):
        self.orch, self.done, self.lock = orch, False, Lock()

    def generate(self, **kw):
        with self.lock:  # four personas run in parallel threads; bump only once
            if not self.done:
                self.done = True
                get_db()["experiments"].update_one({"_id": self.orch.experiment_id}, {"$inc": {"generation": 1}})
        return super().generate(**kw)


def result_for(job, points, map_name="kino"):
    return dict(job_id=job["job_id"], strategy_id=job["strategy_id"], worker="test-worker", map=map_name,
                points=points, survival_s=100.0,
                diagnostics={"overshoot_rate": 0.5, "time_no_target_pct": 30, "last_5s_summary": "synthetic"})


def drain(orch, points_by_kind):
    """Two workers claim every queued job in parallel, then report synthetic results."""
    claimed = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        def worker(name):
            got = []
            while (job := orch.claim_job(name)) is not None:
                got.append(job)
            return got
        for got in pool.map(worker, ["laptop-1", "laptop-2"]):
            claimed.extend(got)
    for job in claimed:
        orch.complete_job(result_for(job, points_by_kind.get(job["kind"], 150)))
    return claimed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    db = get_db()
    exp_id = f"test-{'live' if args.live else 'mock'}-{datetime.now():%H%M%S}"
    orch = MongoOrchestrator(exp_id)
    q = {"experiment_id": exp_id}
    try:
        orch.init_experiment("kino", notes="integration test, synthetic results")
        check("init: baseline playbook + 2 queued jobs",
              db[PLAYBOOKS].count_documents(q) == 1 and db[JOBS].count_documents({**q, "status": "queued"}) == 2)

        try:
            orch.propose(MockClient())
            check("propose refuses while jobs are unfinished", False)
        except ValueError:
            check("propose refuses while jobs are unfinished", True)

        claimed = drain(orch, {"baseline": 100})
        check("two workers claimed each baseline job exactly once",
              len(claimed) == 2 and len({j["job_id"] for j in claimed}) == 2)
        check("runs stored with fitness = points + 5 x survival",
              [r["fitness"] for r in db[RUNS].find(q)] == [600.0, 600.0])

        replay = result_for(claimed[0], 100)
        check("exact replay of a result is a no-op", orch.complete_job(replay) is False)
        try:
            orch.complete_job({**replay, "points": 999})
            check("conflicting replay is rejected", False)
        except ValueError:
            check("conflicting replay is rejected", True)
        try:
            orch.complete_job(result_for(claimed[0], 100, map_name="der-riese"))
            check("result from another map is rejected", False)
        except ValueError:
            check("result from another map is rejected", True)

        try:
            orch.propose(RacingClient(orch))
            check("optimistic lock blocks a concurrent proposer", False)
        except RuntimeError:
            saved = db[PLAYBOOKS].count_documents(q)
            check("optimistic lock blocks a concurrent proposer, nothing saved", saved == 1, f"playbooks={saved}")
            db["experiments"].update_one({"_id": exp_id}, {"$inc": {"generation": -1}})

        client = MockClient()
        if args.live:
            from luminal_client import LuminalClient
            client = LuminalClient()
        out = orch.propose(client)
        check("generation 1: 4 children + 4 child jobs + 1 champion re-evaluation",
              out["generation"] == 1 and len(out["children"]) == 4
              and sorted(j["kind"] for j in out["jobs"]) == ["champion_reevaluation"] + ["child"] * 4)
        check("champion marked is_best and generation history saved",
              db[PLAYBOOKS].find_one({**q, "is_best": True})["_id"] == orch.baseline_id()
              and db[GENERATIONS].count_documents(q) == 1)
        if args.live:
            calls = list(db[LLM_CALLS].find(q))
            ok_calls = [c for c in calls if c.get("ok")]
            fresh = [c for c in out["children"] if not c["fallback"]]
            check("Luminal calls logged in llm_calls", len(calls) >= 4,
                  f"{len(calls)} attempts, {len(ok_calls)} ok, model={ok_calls[0]['model'] if ok_calls else None}")
            check("at least one child came from Luminal (not a fallback)", len(fresh) >= 1,
                  f"{len(fresh)}/4 fresh")
            for c in out["children"]:
                print(f"     {c['persona']:<8} changed {c['changed_params']}: {c['rationale'][:110]}")

        drain(orch, {"baseline": 100, "champion_reevaluation": 140, "child": 160})
        status = orch.status()
        check("status: champion re-evaluated, top two parents ranked",
              len(status["top_parents"]) == 2 and status["jobs"] == {"done": 7}, str(status["top_parents"]))

        out2 = orch.propose(MockClient())
        child2 = out2["children"][0]
        lineage = [p["_id"] for p in memory.playbook_lineage(child2["_id"])]
        check("generation 2 lineage walks back to the baseline via $graphLookup",
              lineage[-1] == orch.baseline_id() and len(lineage) >= 2, f"depth {len(lineage)}")
        board = orch.leaderboard()
        check("leaderboard aggregation ranks by mean fitness",
              board == sorted(board, key=lambda r: -r["mean_fitness"]) and board[0]["mean_fitness"] == 660,
              f"top={board[0]['persona']} {board[0]['mean_fitness']}")
    finally:
        if args.keep:
            print(f"\nkept experiment {exp_id} in MongoDB")
        else:
            orch.delete_experiment()
            print(f"\ncleaned up experiment {exp_id}")
    print(f"all {passed} checks passed")


if __name__ == "__main__":
    main()
