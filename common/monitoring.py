"""Best-effort dashboard telemetry. Writes happen off the game/LLM threads."""
from datetime import datetime, timezone
from queue import Queue, Empty, Full
from threading import Thread

class Reporter:
    def __init__(self, db, experiment_id, component, identity):
        self.db, self.experiment_id = db, experiment_id
        self.component, self.identity = component, identity
        self.queue = Queue(maxsize=32)
        self.thread = Thread(target=self._work, daemon=True)
        self.thread.start()

    def emit(self, **payload):
        doc = dict(experiment_id=self.experiment_id, component=self.component,
                   identity=self.identity, updated_at=datetime.now(timezone.utc), **payload)
        try:
            self.queue.put_nowait(doc)
        except Full:
            # Drop an old heartbeat rather than slowing the control loop.
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                self.queue.put_nowait(doc)
            except (Empty, Full):
                pass

    def _work(self):
        from pymongo import timeout
        while True:
            doc = self.queue.get()
            try:
                if doc is None:
                    return
                with timeout(2):
                    identity = doc.get('persona', self.identity)
                    self.db['dashboard_status'].replace_one(
                        {'_id': f'{self.experiment_id}:{self.component}:{identity}'}, doc, upsert=True)
            except Exception:
                pass  # Monitoring outages must not interrupt gameplay or generation.
            finally:
                self.queue.task_done()

    def close(self):
        try:
            self.queue.put_nowait(None)
        except Full:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                self.queue.put_nowait(None)
            except (Full, Empty):
                return
        self.thread.join(timeout=2.5)
