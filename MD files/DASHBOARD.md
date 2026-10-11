# Backend dashboard

The dashboard shows the real-game evolution loop: vision/controller → MongoDB →
four Luminal agents → queued evaluations → vision/controller. It is a local Python
HTTP service and a browser interface; no frontend build or Windows dependencies are
needed on the dashboard laptop.

## Start

From the repository root:

```sh
.venv/bin/python -m pip install -r requirements-dashboard.txt
.venv/bin/python -m dashboard.server
```

Open http://127.0.0.1:8765. The default is live mode. Supply `MONGODB_URI` and optional
`MONGODB_DB` in the private `.env`, then select your experiment. The dashboard only
reads MongoDB; it cannot start a game, trigger model calls, or modify jobs.

To preview without credentials:

```sh
.venv/bin/python -m dashboard.server --demo
```

The visible demo banner identifies synthetic metrics and a recorded sample image.
Demo mode never calls Luminal or writes to MongoDB. You can select Demo preview or
Live backend in the browser. Restart the server after changing `.env`.
Use `--port 8766` if the default port is already occupied.

## What each panel shows

- **Data flow:** vision worker, MongoDB connection, and Luminal generation status,
  with the strategy/job feedback path back to the game worker.
- **Worker viewport:** latest annotated 640×360 game thumbnail, loop FPS, confirmed
  detections, current action, points gained, and elapsed episode time.
- **Four agents:** Bold, Tuner, Defender, Explorer; current attempt status plus last
  saved call latency. Click an agent for its latest persisted rationale and changes.
- **MongoDB activity:** scoped document counts for jobs, runs, playbooks, and calls.
- **Learning history:** best and mean *run* fitness grouped by evaluation generation,
  and the highest mean strategy fitness among the most recent 500 evaluations.
- **Activity stream:** saved runs, job states, and call attempts. Filter by MongoDB
  job/run records or Luminal call records. This is polling, not a change-stream audit.

The browser polls about every two seconds; responses are cached briefly. Historical
queries are bounded to 500 runs/jobs/playbooks and 200 calls, while collection counts
and job-status totals cover the selected experiment. A completed attempt reflects
provider/validation success; the rationale appears after the orchestrator commits
its transaction. Prior saved metadata can remain visible while the next call runs.

**Pause pauses only the dashboard's automatic refresh.** It does not stop the bot,
Luminal calls, database writes, or work in Codex. Use the worker's F8 abort to stop
an active game episode, and the terminal running the orchestrator to stop that
process. Refresh can manually fetch a snapshot while the dashboard is paused.

## Live instrumentation

Run the updated worker and orchestrator code from this branch on the game machine.
The existing commands still apply:

```sh
python -m bot.worker run --experiment YOUR_EXPERIMENT --propose --generations 8
```

For manual orchestration, `python mongo_orchestrator.py --experiment YOUR_EXPERIMENT
propose` also publishes agent progress. Workers must run with `--experiment` to send
live vision telemetry. Historical results remain visible from older workers, but
live thumbnails and heartbeats require the updated instrumentation.

The new `dashboard_status` collection stores one current document per experiment
and worker, or experiment and persona. Worker frames and progress are updated at
most once every two seconds. Agent signals are emitted at request start, retry,
success, and fallback. The default `_id` index is enough for these upserts; no setup
migration is required. The worker/orchestrator database user needs write permission
for this collection; the dashboard user only needs read permission.

Writes run on a bounded background queue with a two-second operation timeout. If
monitoring is unavailable, the game and generator continue. No raw provider errors,
API keys, or connection strings are exposed by the HTTP API.

A running worker heartbeat older than 12 seconds becomes **stale**; a pending agent
signal older than 240 seconds becomes **stale**. Missing signals mean waiting, not
proof that a process is alive. Frames are hidden when no recent running worker is
observed. Terminal worker states remain complete/aborted/stopped.

## Local endpoints and verification

The server binds only to `127.0.0.1`. Open it on the same laptop; it can read an Atlas
cluster shared with remote game workers. It has no authentication and should not be
exposed publicly without an authenticated reverse proxy.

- `GET /api/experiments`: experiments using the bot schema.
- `GET /api/snapshot?experiment=ID&mode=live`: sanitized, experiment-scoped snapshot.
- `GET /api/frame?experiment=ID&mode=live`: recent JPEG or HTTP 204.
- `mode=demo` on snapshot requests: explicitly labeled synthetic data.

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check dashboard/app.js
```

Tests cover freshness, fallback states, statistics, frame exclusion, demo isolation,
per-persona monitoring documents, and nonfatal database failures. Browser preview
verifies the layout; a real Windows worker + Atlas + Luminal integration run is
still required to verify the complete deployment.
