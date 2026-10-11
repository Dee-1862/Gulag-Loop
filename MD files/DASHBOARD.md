# Backend dashboard

The dashboard shows the real-game evolution loop: vision/controller → MongoDB →
four Luminal agents → MongoDB → queued evaluations → vision/controller. It is a local Python
HTTP service and a browser interface; no frontend build or Windows dependencies are
needed on the dashboard laptop.

## Start

From the repository root:

```sh
.venv/bin/python -m pip install -r requirements-dashboard.txt
.venv/bin/python -m dashboard.server
```

Open http://127.0.0.1:8765. The default is live mode. Supply `MONGODB_URI` and optional
`MONGODB_DB` in the private `.env`, then select your experiment. The data views read MongoDB. The separate **Game controls** menu can start or stop
a local worker, which can write results and request Luminal proposals. Use
`--read-only` (or `DASHBOARD_READ_ONLY=1`) to disable those controls for a display-only
server. Pausing updates never stops gameplay.

To preview without credentials:

```sh
.venv/bin/python -m dashboard.server --demo
```

The visible demo banner identifies synthetic metrics and a recorded sample image.
Demo mode never calls Luminal or writes to MongoDB. You can select Demo preview or
Live backend in the browser. Restart the server after changing `.env`.
Use `--port 8766` if the default port is already occupied.

## Judge-friendly dashboard

- **Overview:** worker viewport, four agent cards, and episode recordings. A compact
  session strip shows the experiment, map, generation, and evaluation count. Game
  controls are tucked into their own menu. Click an agent for its saved rationale
  and exact parent → proposed setting changes.
- **Data flow:** exactly three layers: Vision & game, MongoDB memory, and the four
  Luminal agents. Four separate two-way arrows connect MongoDB to Bold, Tuner,
  Defender, and Explorer. A two-way arrow connects vision and MongoDB. Click or
  keyboard-select a node or connection for current evidence, the proposed changes,
  relevant console lines, and expandable saved events and implementation details.
  The arrows describe the implemented paths; highlighting follows your selection,
  not measured network traffic. The orchestrator performs the database operations.
  Open http://127.0.0.1:8765/#flow.
- **MongoDB records:** points, survival, fitness, and key diagnostics for a saved
  evaluation, alongside a strategy's rationale and changed settings. Expand the
  full settings, diagnostics, lineage, evaluation queue, or call history only when
  needed. Select a completed job to inspect its run; strategy links open the saved
  strategy. Open http://127.0.0.1:8765/#records.

Agent suggestions are hypotheses, not proof of improvement. Comparisons use the
first (assigned) parent; missing parents are explicitly unavailable. Each agent's
latest saved proposal keeps its own generation label even if another call is in
progress. Fallbacks are labeled. The four personas share the configured model.

**Project console** displays captured stdout from a local worker launched by this
running dashboard server, filtered for the selected stage and experiment. It reads
at most the last 64 KiB / 48 lines, redacts URLs and common credential patterns, and
displays at most 12 matching lines. The agents show generation-level stdout; their
individual outcomes are in saved call events. Remote workers, externally launched
processes, and workers from an earlier server session may have no console capture.
The UI says so rather than inventing output. Saved MongoDB events are separately
labeled as records, not stdout. Demo mode never displays actual process logs.

Snapshots refresh approximately every two seconds. Pause freezes snapshot views;
local process status continues to poll for the game controls. MongoDB queries are
bounded to 500 runs/jobs/playbooks and 200 calls; full collection counts and queue
totals cover the selected experiment. Records show up to 25 runs, 50 strategies,
50 jobs, and 20 call attempts. Parent settings are included when the parent exists
in the 500-playbook sample, even outside the visible 50-row window. Strategy scores
use the recent run sample. Selection persists across polling and resets when the
experiment changes. Recordings clear immediately on source or experiment changes.

For a synthetic preview, open http://127.0.0.1:8765/?mode=demo#flow. Demo data and
sample frames remain visibly labeled; no gameplay, database writes, or model calls
are started by demo preview.

**Pause pauses only the dashboard's automatic refresh.** It does not stop the bot,
Luminal calls, database writes, or work in Codex. Use the worker's F8 abort to stop
an active game episode, and the terminal running the orchestrator to stop that
process. Refresh can manually fetch a snapshot while the dashboard is paused.
Pausing also discards requests already in flight. Switching data source or
experiment discards earlier responses so they cannot overwrite the selected view.
The footer shows the snapshot time, rather than the time you pressed Pause.

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
monitoring is unavailable, the game and generator continue. Snapshots exclude raw provider errors, API keys, and connection strings. Local
console lines use the bounded, redacted capture described above.

A running worker heartbeat older than 12 seconds becomes **stale**; a pending agent
signal older than 240 seconds becomes **stale**. Missing signals mean waiting, not
proof that a process is alive. Frames are hidden when no recent running worker is
observed. Terminal worker states remain complete/aborted/stopped.
The viewport follows the most recently updated running worker (or most recent
worker if none is running). Its image is fetched for that worker and job only;
missing images are not labeled live. Elapsed time is shown without assuming a
fixed episode duration.

## Local endpoints and verification

The server binds only to `127.0.0.1`. Open it on the same laptop; it can read an Atlas
cluster shared with remote game workers. It has no authentication and should not be
exposed publicly without an authenticated reverse proxy.

- `GET /api/experiments`: experiments using the bot schema.
- `GET /api/snapshot?experiment=ID&mode=live`: sanitized, experiment-scoped snapshot.
- `GET /api/frame?experiment=ID&mode=live&worker=IDENTITY&job=JOB_ID`: the selected
  running worker's recent JPEG or HTTP 204. Worker identity is required; supplying
  the job ID prevents a newer episode's frame from appearing with older statistics.
- `GET /api/video?experiment=ID&job=JOB_ID&mode=live`: saved GridFS MP4 playback.
- `GET /api/control/status`: local process state and bounded console tail, when enabled.
- `mode=demo` on snapshot requests: explicitly labeled synthetic data.

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check dashboard/app.js
```

Optional interface regression checks use jsdom outside the project (no frontend
runtime dependency):

```sh
npm install --prefix /tmp/zombieloop-dashboard-tests --no-save jsdom
NODE_PATH=/tmp/zombieloop-dashboard-tests/node_modules node --test tests/dashboard_ui.cjs
```

These check late responses during source changes and pausing, worker/frame matching,
missing-image and fallback labels, all five graph arrow endpoints, the three-layer
structure, exact setting comparisons, console isolation, recording reset, and
keyboard tab/graph controls.

Tests cover freshness, fallback states, statistics, frame exclusion, demo isolation,
per-persona monitoring documents, and nonfatal database failures. Browser preview
verifies the layout; a real Windows worker + Atlas + Luminal integration run is
still required to verify the complete deployment.
