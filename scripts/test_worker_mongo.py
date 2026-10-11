"""Run the real bot/worker.py loop against MongoDB with the game, vision and mouse faked out.

    python scripts/test_worker_mongo.py            # mock agents
    python scripts/test_worker_mongo.py --live     # proposals from real Luminal (billable)

Checks the wiring only: claim -> episode -> complete -> auto-propose -> next generation.
Episode results are SYNTHETIC. Nothing touches the mouse, keyboard or browser.
"""
import argparse
import random
import sys
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot.worker as worker
import vision.capture
from common.db import GENERATIONS, JOBS, LLM_CALLS, PLAYBOOKS, RUNS, get_db
from mongo_orchestrator import MongoOrchestrator


class FakeGame:
    def __init__(self, *a, **kw): pass
    def open(self): return self
    def gpu(self): return "fake GPU"
    def start(self): pass
    def align(self): return (0, 0, 1280, 720)
    def canvas_region(self): return (0, 0, 1280, 720)
    def close(self): pass


class FakeCapture:
    backend = "fake"
    def __init__(self, *a, **kw): pass
    def close(self): pass


class FakeVision:
    def __init__(self):
        self.tracker = types.SimpleNamespace(__init__=lambda: None)

    @classmethod
    def from_config(cls, *a, **kw):
        return cls()


class FakeHud:
    def __init__(self, *a, **kw):
        self.points_hist, self.points = [], None


episodes = []


def fake_episode(game, vp, hud, params, abort_first=False, **kw):
    """Fitness rises with aim_gain so ranking has something to sort; the first call can simulate F8."""
    episodes.append(params)
    if abort_first and len(episodes) == 1:
        return {"points": 0, "survival_s": 1.0, "game_over": False, "aborted": True, "diagnostics": {}}
    points = int(100 + 200 * params["aim_gain"] + random.randint(0, 20))
    return {"points": points, "survival_s": 120.0, "game_over": True, "aborted": False,
            "diagnostics": {"overshoot_rate": 0.4, "time_no_target_pct": 35.0, "close_calls": 12}}


def run_worker(argv):
    sys.argv = ["bot.worker"] + argv
    worker.main()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    random.seed(3)

    # Fake everything that touches the screen, GPU, browser or mouse.
    worker.PortGame = FakeGame
    vision.capture.Capture = FakeCapture
    sys.modules["vision.pipeline"] = types.SimpleNamespace(VisionPipeline=FakeVision)
    sys.modules["bot.hudread"] = types.SimpleNamespace(HudReader=FakeHud)
    worker.resolve_weights = lambda configured: "fake-weights.pt"
    if not args.live:
        import luminal_client
        from scripts.test_mongo_luminal import MockClient
        luminal_client.LuminalClient = lambda *a, **kw: MockClient()

    exp_id = f"test-worker-{'live' if args.live else 'mock'}-{datetime.now():%H%M%S}"
    orch = MongoOrchestrator(exp_id)
    db, q = get_db(), {"experiment_id": exp_id}
    common = ["run", "--experiment", exp_id, "--map", "kino", "--worker", "fake-laptop", "--wait-s", "0",
              "--save-frames", "", "--out-dir", str(ROOT / "local_data" / "test-episodes")]
    ok = True
    try:
        orch.init_experiment("kino", notes="worker wiring test, synthetic episodes")

        # 1) F8 on the first episode: the job must go back to the queue, nothing stored.
        worker.run_episode = lambda *a, **kw: fake_episode(*a, abort_first=True, **kw)
        run_worker(common)
        queued = db[JOBS].count_documents({**q, "status": "queued"})
        print(("PASS" if queued == 2 and db[RUNS].count_documents(q) == 0 else "FAIL"),
              f"F8 abort returns the job to the queue (queued={queued})")
        ok &= queued == 2

        # 2) Full run: 2 baselines, then auto-propose generations 1 and 2, 5 jobs each.
        worker.run_episode = fake_episode
        run_worker(common + ["--propose", "--generations", "2"])
        st = orch.status()
        runs = db[RUNS].count_documents(q)
        good = st["generation"] == 2 and st["jobs"] == {"done": 12} and runs == 12
        print(("PASS" if good else "FAIL"), f"worker claimed, played and stored every job; auto-proposed 2 generations "
              f"(generation={st['generation']}, runs={runs}, jobs={st['jobs']})")
        ok &= good
        gens = db[GENERATIONS].count_documents(q)
        print(("PASS" if gens == 2 else "FAIL"), f"generation history saved ({gens} rows)")
        ok &= gens == 2
        if args.live:
            calls = db[LLM_CALLS].count_documents({**q, "ok": True})
            print(("PASS" if calls >= 8 else "FAIL"), f"Luminal calls logged: {calls} ok")
            ok &= calls >= 8
        for row in orch.leaderboard(3):
            print(f"     {row['persona'] or '?':<9} gen {row['generation']}  mean fitness {row['mean_fitness']:.0f}"
                  f"  ({row['runs']} runs)")
    finally:
        orch.delete_experiment()
        print(f"\ncleaned up experiment {exp_id}")
    print("all checks passed" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
