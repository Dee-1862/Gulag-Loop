"""Start and stop the local game worker from the dashboard. Local only; disabled with --read-only.

One worker at a time, launched exactly as you would by hand:
    python -m bot.worker run --experiment EXP --slot 0 --stream --headless --propose --generations N
so the game runs with no window and is watched in the dashboard's live viewport.
"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "local_data"


def worker_processes():
    """Every bot.worker process on this machine, including ones started from a terminal: [(pid, experiment)]."""
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        cmd = p.info.get("cmdline") or []
        if "bot.worker" in cmd and "run" in cmd:
            exp = cmd[cmd.index("--experiment") + 1] if "--experiment" in cmd[:-1] else None
            out.append((p.info["pid"], exp))
    # the .venv launcher and the interpreter it starts share a command line: keep the parent only
    pids = {pid for pid, _ in out}
    try:
        return [(pid, exp) for pid, exp in out if psutil.Process(pid).ppid() not in pids]
    except Exception:
        return out


def _python():
    venv = ROOT / ".venv" / "Scripts" / "python.exe"  # the game needs the CUDA/TensorRT environment
    return str(venv) if venv.exists() else sys.executable


class WorkerControl:
    def __init__(self):
        self.lock = Lock()
        self.proc = None
        self.experiment = None
        self.started_at = None
        self.log_path = None

    def running(self):
        return (self.proc is not None and self.proc.poll() is None) or bool(worker_processes())

    def status(self):
        with self.lock:
            tail = []
            if self.log_path and self.log_path.exists():
                try:
                    lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    tail = [l for l in lines if l.strip()][-4:]
                except OSError:
                    pass
            others = worker_processes()
            own = self.proc is not None and self.proc.poll() is None
            if not own and others:  # started from a terminal: still controllable here
                pid, exp = others[0]
                return {"enabled": True, "running": True, "experiment": exp, "pid": pid, "external": True,
                        "started_at": None, "exit_code": None, "log_tail": tail}
            return {"enabled": True, "running": own, "experiment": self.experiment,
                    "pid": self.proc.pid if self.proc else None, "started_at": self.started_at,
                    "exit_code": None if self.proc is None or self.running() else self.proc.returncode,
                    "log_tail": tail}

    def start(self, experiment, generations=2, map_name="kino"):
        experiment = (experiment or "").strip()
        if not experiment or len(experiment) > 64 or not all(c.isalnum() or c in "-_:" for c in experiment):
            raise ValueError("Experiment names use letters, digits, - _ : (max 64)")
        generations = max(1, min(int(generations), 20))
        with self.lock:
            if self.running():
                raise RuntimeError(f"A game worker is already running ({self.experiment})")
            sys.path.insert(0, str(ROOT))
            from mongo_orchestrator import MongoOrchestrator
            orch = MongoOrchestrator(experiment)
            if orch.db["experiments"].find_one({"_id": experiment}) is None:
                orch.init_experiment(map_name, notes="started from the dashboard")
            # Jobs this laptop left "running" (crash, closed window) go back in the queue.
            orch.db["jobs"].update_many(
                {"experiment_id": experiment, "status": "running", "worker": socket.gethostname()},
                {"$set": {"status": "queued"}, "$unset": {"worker": "", "claimed_at": ""}})
            LOGS.mkdir(exist_ok=True)
            self.log_path = LOGS / f"worker_{experiment.replace(':', '_')}.log"
            cmd = [_python(), "-u", "-m", "bot.worker", "run", "--experiment", experiment, "--map", map_name,
                   "--slot", "0", "--stream", "--headless", "--propose", "--generations", str(generations)]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            log = open(self.log_path, "w", encoding="utf-8")
            self.proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=flags, env={**os.environ, "PYTHONUNBUFFERED": "1"})
            self.experiment = experiment
            self.started_at = datetime.now(timezone.utc).isoformat()
            return self.status_unlocked()

    def status_unlocked(self):
        return {"enabled": True, "running": self.running(), "experiment": self.experiment,
                "pid": self.proc.pid if self.proc else None, "started_at": self.started_at}

    def stop(self):
        with self.lock:
            own = self.proc is not None and self.proc.poll() is None
            targets = [(self.proc.pid, self.experiment)] if own else worker_processes()
            if not targets:
                return {"enabled": True, "running": False, "experiment": self.experiment}
            pid, exp = targets[0]
            self.experiment = exp or self.experiment
            if os.name == "nt":  # the worker's headless Chrome is a child process: end the whole tree
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            elif own:
                self.proc.terminate()
            if own:
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            time.sleep(0.5)
            try:  # the game it was playing goes back in the queue
                sys.path.insert(0, str(ROOT))
                from common.db import get_db
                get_db()["jobs"].update_many(
                    {"experiment_id": self.experiment, "status": "running", "worker": socket.gethostname()},
                    {"$set": {"status": "queued"}, "$unset": {"worker": "", "claimed_at": ""}})
            except Exception:
                pass
            return {"enabled": True, "running": False, "experiment": self.experiment, "stopped_pid": pid}
