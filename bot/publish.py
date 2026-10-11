"""Publish the bot's experiment to the team MongoDB so the dashboard (origin/jeffrey: dashboard/) shows it.

Writes only the shapes dashboard/data.py reads, all scoped by experiment_id:
  experiments {_id, design:'bot', map, generation, started_at}
  playbooks   {_id, experiment_id, persona, generation, model, rationale, parent_ids, changed_params, fallback,
               fallback_source, is_best, created_at, params}
  jobs        {_id=job_id, job_id, experiment_id, strategy_id, generation, kind, status, worker, created_at, completed_at}
  runs        {_id=job_id, job_id, experiment_id, strategy_id, generation, worker, map, points, survival_s, fitness,
               diagnostics, completed_at}
  llm_calls   {experiment_id, persona, model, generation, strategy_id, ok, attempt, tokens, latency_ms, created_at, ...}
  dashboard_status {experiment_id, component:'vision', identity, worker, state, updated_at, fps, detections, action,
                    points, elapsed_s, job_id, frame(base64 JPEG 640x360)}   <- live "Worker viewport"
Plus footage the dashboard can adopt: GridFS bucket 'footage' (episode HUD videos) + 'footage' docs keyed by job_id.

Credentials come from CZB_HOME/.env (MONGODB_URI, MONGODB_DB); never from the repo.
"""
import base64
import os
import socket
import time
from datetime import datetime, timezone

import cv2


def _now():
    return datetime.now(timezone.utc)


def _dt(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def load_mongo_config():
    from bot.paths import p
    cfg = {}
    env = p(".env")
    if os.path.exists(env):
        for line in open(env):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                cfg[k] = v
    uri = os.environ.get("MONGODB_URI") or cfg.get("MONGODB_URI")
    db = os.environ.get("MONGODB_DB") or cfg.get("MONGODB_DB", "zombieloop")
    return uri, db


class Publisher:
    def __init__(self, experiment_id, map_id="kino", uri=None, db_name=None, identity=None, notes=None):
        from pymongo import MongoClient
        cfg_uri, cfg_db = load_mongo_config()
        uri, db_name = uri or cfg_uri, db_name or cfg_db
        if not uri:
            raise RuntimeError("MONGODB_URI not set (put it in CZB_HOME/.env)")
        kw = {}
        if uri.startswith("mongodb+srv"):
            import certifi
            kw["tlsCAFile"] = certifi.where()
        self.client = MongoClient(uri, serverSelectionTimeoutMS=8000, appname="zombie-bot-publisher", **kw)
        self.db = self.client[db_name]
        self.db.command("ping")
        self.exp = experiment_id
        self.map = map_id
        self.identity = identity or f"{socket.gethostname()}-vision"
        self.worker = socket.gethostname()
        self._last_beat = 0.0
        self._started = time.time()
        self.db.experiments.update_one({"_id": self.exp}, {"$setOnInsert": {
            "design": "bot", "map": map_id, "generation": 0, "started_at": _now(),
            "notes": notes or "browser-port bot (bot/loop.py), published by bot/publish.py"}}, upsert=True)

    # ---------- evolution state mirror ----------
    def sid(self, strategy_id):
        """Team convention: fixed names are namespaced per experiment ('exp-kino-1:baseline'); UUIDs stay global."""
        s = str(strategy_id)
        if len(s) == 36 and s.count("-") == 4 or s.startswith(self.exp + ":"):
            return s
        return f"{self.exp}:{s}"

    def sync_state(self, state):
        """Idempotent mirror of a local evolution.py state into the dashboard collections."""
        from pymongo import UpdateOne
        q = {"experiment_id": self.exp}
        now = _now()
        jobs = {j["job_id"]: j for j in state["jobs"]}
        best = None
        means = {}
        for r in state["runs"]:
            means.setdefault(self.sid(r["strategy_id"]), []).append(r["points"] + 5 * r["survival_s"])
        if means:
            best = max(means, key=lambda k: sum(means[k]) / len(means[k]))
        pb_ops = []
        for c in state["strategies"]:
            pb_ops.append(UpdateOne({"_id": self.sid(c["_id"])}, {"$set": {
                **q, "persona": c.get("persona"), "generation": c.get("generation", 0), "model": c.get("model"),
                "rationale": c.get("rationale"), "parent_ids": [self.sid(x) for x in c.get("parent_ids", [])],
                "changed_params": c.get("changed_params", []), "fallback": c.get("fallback", False),
                "fallback_source": c.get("fallback_source"), "params": c.get("params"),
                "is_best": self.sid(c["_id"]) == best}, "$setOnInsert": {"created_at": _dt(c.get("created_at")) or now}},
                upsert=True))
        job_ops = []
        done = {r["job_id"] for r in state["runs"]}
        for j in state["jobs"]:
            job_ops.append(UpdateOne({"_id": j["job_id"]}, {"$set": {
                **q, "job_id": j["job_id"], "strategy_id": self.sid(j["strategy_id"]), "generation": j.get("generation", 0),
                "kind": j.get("kind"), "status": "done" if j["job_id"] in done else j.get("status", "queued"),
                "worker": self.worker if j["job_id"] in done else None},
                "$setOnInsert": {"created_at": now}}, upsert=True))
        run_ops = []
        for r in state["runs"]:
            run_ops.append(UpdateOne({"_id": r["job_id"]}, {"$set": {
                **q, "job_id": r["job_id"], "strategy_id": self.sid(r["strategy_id"]),
                "generation": jobs.get(r["job_id"], {}).get("generation", 0), "worker": r.get("worker"),
                "map": r.get("map"), "points": r["points"], "survival_s": r["survival_s"],
                "fitness": r["points"] + 5 * r["survival_s"], "diagnostics": r.get("diagnostics", {})},
                "$setOnInsert": {"completed_at": now}}, upsert=True))
            self.db.jobs.update_one({"_id": r["job_id"], "completed_at": {"$exists": False}},
                                    {"$set": {"completed_at": now}})
        call_ops = []
        for i, c in enumerate(state.get("llm_calls", [])):
            key = f"{self.exp}:{c.get('strategy_id')}:{c.get('attempt')}:{c.get('persona')}:{i}"
            call_ops.append(UpdateOne({"_id": key}, {"$set": {
                **q, **{k: c.get(k) for k in ("persona", "model", "generation", "ok", "attempt",
                                               "tokens", "latency_ms", "error_type", "http_status")},
                "strategy_id": self.sid(c.get("strategy_id")),
                "created_at": _dt(c.get("created_at")) or now}}, upsert=True))
        for coll, ops in (("playbooks", pb_ops), ("jobs", job_ops), ("runs", run_ops), ("llm_calls", call_ops)):
            if ops:
                self.db[coll].bulk_write(ops, ordered=False)
        self.db.experiments.update_one({"_id": self.exp}, {"$set": {"generation": state.get("generation", 0)}})

    # ---------- live worker viewport ----------
    def heartbeat(self, job_id, frame_bgr, fps=None, detections=None, action=None, points=None,
                  elapsed_s=None, every_s=2.0, force=False):
        now = time.time()
        if not force and now - self._last_beat < every_s:
            return
        self._last_beat = now
        doc = {"experiment_id": self.exp, "component": "vision", "identity": self.identity, "worker": self.worker,
               "state": "running", "updated_at": _now(), "job_id": job_id, "fps": fps, "detections": detections,
               "action": action, "points": points, "elapsed_s": elapsed_s}
        if frame_bgr is not None:
            small = cv2.resize(frame_bgr, (640, 360), interpolation=cv2.INTER_AREA)
            ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                doc["frame"] = base64.b64encode(jpg.tobytes()).decode("ascii")
        try:
            self.db.dashboard_status.update_one({"experiment_id": self.exp, "component": "vision",
                                                 "identity": self.identity}, {"$set": doc}, upsert=True)
        except Exception:
            pass  # the game must never stall on the dashboard

    def idle(self):
        try:
            self.db.dashboard_status.update_one({"experiment_id": self.exp, "component": "vision",
                                                 "identity": self.identity},
                                                {"$set": {"state": "idle", "updated_at": _now()}, "$unset": {"frame": 1}})
        except Exception:
            pass

    # ---------- footage ----------
    def upload_footage(self, job_id, path, kind="hud_video", meta=None, max_w=640):
        """Re-encode to a small streamable mp4 (640 wide, faststart) and store it in GridFS 'footage'."""
        import subprocess
        import tempfile
        import gridfs
        import imageio_ffmpeg
        if not path or not os.path.exists(path):
            return None
        if self.db.footage.find_one({"_id": f"{job_id}:{kind}"}):
            return None
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        try:
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-i", path, "-vf",
                            f"scale={max_w}:-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                            "-movflags", "+faststart", "-an", tmp.name], check=True)
            fs = gridfs.GridFSBucket(self.db, bucket_name="footage")
            with open(tmp.name, "rb") as f:
                fid = fs.upload_from_stream(f"{job_id}.mp4", f, metadata={
                    "experiment_id": self.exp, "job_id": job_id, "kind": kind, "contentType": "video/mp4"})
            size = os.path.getsize(tmp.name)
        finally:
            os.unlink(tmp.name)
        self.db.footage.update_one({"_id": f"{job_id}:{kind}"}, {"$set": {
            "experiment_id": self.exp, "job_id": job_id, "kind": kind, "gridfs_bucket": "footage", "file_id": fid,
            "bytes": size, "created_at": _now(), **(meta or {}),
            **({"strategy_id": self.sid(meta["strategy_id"])} if meta and meta.get("strategy_id") else {})}},
            upsert=True)
        return fid


def main():
    """Backfill a local evolution state (+ its episode videos) into the dashboard database.

    python -m bot.publish --state D:/czb/evo_state/kino_v6.json            # team Atlas from CZB_HOME/.env
    python -m bot.publish --state ... --uri mongodb://127.0.0.1:27018/?directConnection=true   # local test
    """
    import argparse
    import glob
    import json
    from bot.paths import p
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--experiment-id", default=None)
    ap.add_argument("--map", default="kino")
    ap.add_argument("--uri", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--episodes", default=p("episodes"))
    ap.add_argument("--no-footage", action="store_true")
    args = ap.parse_args()
    exp = args.experiment_id or "rishit-" + os.path.splitext(os.path.basename(args.state))[0]
    pub = Publisher(exp, args.map, uri=args.uri, db_name=args.db)
    state = json.load(open(args.state))
    pub.sync_state(state)
    up = 0
    if not args.no_footage:
        vids = {os.path.basename(v)[:-4]: v for v in glob.glob(os.path.join(args.episodes, "*.mp4"))}
        for r in state["runs"]:
            if r["job_id"] in vids:
                up += bool(pub.upload_footage(r["job_id"], vids[r["job_id"]], meta={
                    "strategy_id": r["strategy_id"], "points": r["points"], "survival_s": r["survival_s"],
                    "kills": r.get("diagnostics", {}).get("kills"), "fitness": r["points"] + 5 * r["survival_s"]}))
    counts = {k: pub.db[k].count_documents({"experiment_id": exp})
              for k in ("playbooks", "jobs", "runs", "llm_calls", "footage")}
    print(f"[publish] experiment '{exp}': {counts}, new videos {up}")


if __name__ == "__main__":
    main()
