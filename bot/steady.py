"""Steady-state evolution: every game is a step. Champion/challenger with confirmation, Luminal personas in rotation.

python -m bot.steady --state D:/czb/evo_state/steady.json --minutes 40 --publish

Each iteration plays ONE game:
  - champion re-evaluation every `reeval_every` iterations (keeps the champion's mean honest), or
  - confirmation of a challenger that just beat the champion's mean, or
  - a new challenger: one Luminal persona (Tuner -> Bold -> Defender -> Explorer, rotating) proposes a change to the
    champion from its latest diagnostics. Failed calls fall back inside the client (cache / local mutation).
A challenger replaces the champion only if the mean of its 2 games beats the champion's mean, so one lucky
game can't win. The champion-mean curve is the improvement signal; every raw game is also kept.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from statistics import mean

from bot.paths import p as _p
from bot.worker import Runner, log

ROTATION = ["Tuner", "Bold", "Defender", "Explorer"]


def fitness(r):
    return r["points"] + 5 * r["survival_s"]


class Steady:
    def __init__(self, path, seed_from=None, seed_id="baseline"):
        self.path = path
        if os.path.exists(path):
            self.s = json.load(open(path))
            return
        from bot.controller import BASELINE
        self.s = {"experiment": "rishit-" + os.path.splitext(os.path.basename(path))[0], "iter": 0,
                  "champion_id": seed_id, "pending_confirm": None, "rotation": 0,
                  "strategies": {seed_id: {"_id": seed_id, "params": dict(BASELINE), "persona": "Baseline",
                                           "rationale": "Hand-written baseline.", "parent_ids": [], "generation": 0,
                                           "fallback": False, "model": None}},
                  "runs": [], "llm_calls": [], "champion_history": []}
        if seed_from:  # reuse earlier evaluations of the seed on the same controller (honest prior, saves time)
            prev = json.load(open(seed_from))
            for c in prev["strategies"]:
                if c["_id"] == seed_id:
                    self.s["strategies"][seed_id]["params"] = c["params"]
            for r in prev["runs"]:
                if r["strategy_id"] == seed_id:  # new job ids: never move another experiment's run documents
                    self.s["runs"].append({**r, "job_id": "seed-" + r["job_id"], "iter": 0, "role": "seed"})
        self._log_champion()
        self.save()

    # ---------- bookkeeping ----------
    def save(self):
        tmp = self.path + ".tmp"
        json.dump(self.s, open(tmp, "w"), indent=1)
        os.replace(tmp, self.path)

    def evals(self, sid):
        return [fitness(r) for r in self.s["runs"] if r["strategy_id"] == sid]

    def champ_mean(self):
        e = self.evals(self.s["champion_id"])
        return mean(e) if e else 0.0

    def _log_champion(self):
        e = self.evals(self.s["champion_id"])
        self.s["champion_history"].append({"iter": self.s["iter"], "champion_id": self.s["champion_id"],
                                           "champion_mean": round(mean(e), 1) if e else None, "n": len(e)})

    def last_diag(self, sid):
        rs = [r for r in self.s["runs"] if r["strategy_id"] == sid]
        return rs[-1]["diagnostics"] if rs else {}

    # ---------- what to play next ----------
    def next_job(self, client):
        s = self.s
        it = s["iter"] + 1
        champ = s["champion_id"]
        if len(self.evals(champ)) < 2 or (it % 5 == 0 and not s["pending_confirm"]):
            return {"strategy_id": champ, "role": "champion"}
        if s["pending_confirm"]:
            return {"strategy_id": s["pending_confirm"], "role": "confirm"}
        persona = ROTATION[s["rotation"] % len(ROTATION)]
        s["rotation"] += 1
        parent = s["strategies"][champ]
        hist = [{"iter": h["iter"], "champion_mean": h["champion_mean"]} for h in s["champion_history"][-6:]]
        t = time.time()
        out = client.generate(parent["params"], self.last_diag(champ), persona=persona, generation=it,
                              parent_id=champ, fitness=round(self.champ_mean(), 1), history=hist)
        child = out["strategy"]
        child.setdefault("parent_ids", [champ])
        child["generation"] = it
        child["changed_params"] = child.get("changed_params") or [
            k for k in child["params"] if child["params"][k] != parent["params"].get(k)]
        s["strategies"][child["_id"]] = child
        for c in out.get("llm_calls", []):
            s["llm_calls"].append({**c, "generation": it})
        log(f"iter {it}: {persona} proposed {child['_id'][:8]} ({'fallback' if child.get('fallback') else 'luminal'}, "
            f"{time.time() - t:.0f}s) changed={child['changed_params']} | {str(child.get('rationale'))[:140]}")
        return {"strategy_id": child["_id"], "role": "challenger"}

    def record(self, job, result):
        s = self.s
        s["iter"] += 1
        r = {**result, "iter": s["iter"], "role": job["role"]}
        s["runs"].append(r)
        sid, champ = job["strategy_id"], s["champion_id"]
        cm = self.champ_mean()
        f = fitness(result)
        verdict = ""
        if job["role"] == "challenger":
            if f > cm:
                s["pending_confirm"] = sid
                verdict = f"beat champion mean {cm:.0f} -> confirmation game next"
            else:
                verdict = f"below champion mean {cm:.0f} -> discarded"
        elif job["role"] == "confirm":
            s["pending_confirm"] = None
            m = mean(self.evals(sid))
            if m > cm:
                s["champion_id"] = sid
                verdict = f"CONFIRMED: 2-game mean {m:.0f} > {cm:.0f} -> NEW CHAMPION"
            else:
                verdict = f"not confirmed: 2-game mean {m:.0f} <= {cm:.0f}"
        else:
            verdict = f"champion re-evaluated -> mean {self.champ_mean():.0f} over {len(self.evals(champ))} games"
        self._log_champion()
        self.save()
        log(f"iter {s['iter']} [{job['role']}] {sid[:12]}: fitness {f:.0f} (+{result['points']} pts, "
            f"{result['survival_s']}s, kills {result['diagnostics'].get('kills')}) | {verdict} | "
            f"champion {s['champion_id'][:12]} mean {self.champ_mean():.0f}")

    # ---------- dashboard ----------
    def as_evo_state(self):
        """Shape bot.publish.Publisher.sync_state expects (generation = iteration)."""
        s = self.s
        strategies = [{**c, "is_champion": c["_id"] == s["champion_id"]} for c in s["strategies"].values()]
        jobs = [{"job_id": r["job_id"], "strategy_id": r["strategy_id"], "generation": r["iter"],
                 "kind": r.get("role", "eval"), "status": "done"} for r in s["runs"]]
        runs = [{k: r[k] for k in ("job_id", "strategy_id", "worker", "map", "points", "survival_s", "diagnostics")
                 if k in r} for r in s["runs"]]
        return {"generation": s["iter"], "strategies": strategies, "jobs": jobs, "runs": runs,
                "llm_calls": s["llm_calls"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=_p("evo_state", "steady.json"))
    ap.add_argument("--seed-from", default=_p("evo_state", "kino_v6.json"), help="reuse seed evaluations from here")
    ap.add_argument("--seed-id", default="baseline")
    ap.add_argument("--evo-dir", default=_p("evo"))
    ap.add_argument("--minutes", type=float, default=40)
    ap.add_argument("--max-s", type=float, default=180)
    ap.add_argument("--map", default="kino")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.evo_dir))
    from bot.loop import make_client
    st = Steady(args.state, args.seed_from if os.path.exists(args.seed_from or "") else None, args.seed_id)
    client, kind = make_client(args.evo_dir, args.offline)
    log(f"steady-state experiment {st.s['experiment']}: champion {st.s['champion_id']} "
        f"mean {st.champ_mean():.0f} over {len(st.evals(st.s['champion_id']))} games; proposals via {kind}")
    pub = None
    if args.publish:
        from bot.publish import Publisher
        pub = Publisher(st.s["experiment"], args.map, notes="steady-state champion/challenger evolution (bot/steady.py)")
        pub.sync_state(st.as_evo_state())
    runner = Runner(args.map, max_s=args.max_s, publisher=pub)
    t_end = time.time() + args.minutes * 60
    try:
        while time.time() < t_end:
            job = st.next_job(client)
            st.save()
            job_doc = {"job_id": f"st-{st.s['iter'] + 1:03d}-{int(time.time())}", "strategy_id": job["strategy_id"],
                       "kind": job["role"], "params": st.s["strategies"][job["strategy_id"]]["params"]}
            try:
                res = runner.run(job_doc)
            except Exception as e:  # browser died mid-game etc.: don't score it, retry the same job
                log(f"game failed ({type(e).__name__}: {str(e)[:80]}); not scored")
                if job["role"] == "challenger":
                    st.s["pending_confirm"] = None
                continue
            if res is None:
                log("aborted (F8); stopping")
                break
            st.record(job, res)
            if pub is not None:
                try:
                    pub.sync_state(st.as_evo_state())
                    pub.upload_footage(res["job_id"], os.path.join(runner.out_dir or "", f"{res['job_id']}.mp4"),
                                       meta={"strategy_id": res["strategy_id"], "generation": st.s["iter"],
                                             "points": res["points"], "survival_s": res["survival_s"],
                                             "kills": res["diagnostics"].get("kills"), "fitness": fitness(res)})
                except Exception as e:
                    log(f"dashboard publish failed (continuing): {type(e).__name__}")
    finally:
        runner.close()
    h = st.s["champion_history"]
    log(f"done: {st.s['iter']} iterations; champion mean {h[0]['champion_mean']} -> {h[-1]['champion_mean']}")


if __name__ == "__main__":
    main()
