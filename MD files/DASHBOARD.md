# Gulag Loop dashboard

A live view of the learning loop: vision/game worker → MongoDB → four Luminal
agents → MongoDB → the next game round.

## Start

```sh
.venv/bin/python -m pip install -r requirements-dashboard.txt
.venv/bin/python -m dashboard.server
```

Open http://127.0.0.1:8765. Configure `MONGODB_URI` and optional `MONGODB_DB` in
`.env`. The existing database name remains `zombieloop` for compatibility with
saved data and teammates' workers; the project/display name is **Gulag Loop**.

The dashboard uses only the live backend. There is no source or experiment
selector and no `--demo` mode. Old browser demo links open the live dashboard;
requests for synthetic API data are rejected. Synthetic fixtures remain available
only to automated tests.

## One combined history

All experiments with `design: bot` are included. The overview follows the most
recently active worker, displays the four personas, and lists the latest episode
recordings. The **MongoDB records** tab has a chronological run stream with the
newest results first. **Load older runs** retrieves another 25 evaluations across
all experiments, so older history remains accessible without selecting experiments.
Click a run to inspect its results. New arrivals are merged without dropping older
runs already loaded. Every run/video keeps its originating experiment label.

The display combines history; the database experiments and evolution loops stay
independent. Parent comparisons, evaluation averages, strategy links, video URLs
and frame requests retain their original experiment identities. Generation numbers
are meaningful only within an experiment. The top bar therefore shows experiment
and evaluation counts instead of a misleading shared generation number.

## Views

- **Overview:** worker viewport, four agent cards, and episode recordings.
- **Data flow:** three clickable layers: Vision & game, MongoDB memory, four Luminal
  agents. Four two-way connections link MongoDB to Bold, Tuner, Defender and Explorer.
  Click a layer, node or connection to see evidence, saved rationales and exact
  parent → proposed setting changes. Proposals are hypotheses until gameplay tests
  them. Each persona shows its most recently updated experiment and that experiment's
  latest saved proposal. Saved proposal generations are explicitly labeled.
- **MongoDB records:** unified run history, selected evaluation outcomes, strategy
  comparisons, and expandable diagnostics, full settings, lineage, queue and call
  history. Queue totals span all experiments; readiness is independent per experiment.

## Console and freshness

Project console shows captured stdout for a local worker launched by this dashboard
server. Vision and persona output is matched to its experiment; shared MongoDB and
agent-layer output shows the local orchestrator. Remote/externally started workers
may have no capture. Saved backend events are separately labeled as records, not
stdout, and include their originating experiment.

Console capture reads at most 64 KiB / 48 lines, redacts URLs and common credential
patterns, and shows at most 12 matching lines. Snapshots exclude raw provider errors,
credentials and frames. Parent settings outside the recent playbook sample are
unavailable, never invented.

Snapshots refresh approximately every two seconds. **Pause updates** freezes
snapshot polling; it does not stop gameplay or model calls. Manual refresh still
works. Game controls separately offer **New run** and **Stop bot**. New run creates
or starts a named experiment; its results join the combined history automatically.
Use `--read-only` or `DASHBOARD_READ_ONLY=1` to disable game controls.

Queries sample 500 runs/jobs/playbooks, 200 calls and 50 status documents. Record
inspectors initially show 25 runs, 50 strategies/jobs, and 20 calls. Full document
counts and queue totals span all bot experiments. Strategy scores use the recent
run sample. Run history can page beyond the initial sample.

Worker telemetry older than 12 seconds and pending agent progress older than
240 seconds are marked stale. The viewport requests the active worker's experiment,
identity and job; historical results do not imply a worker is currently playing.

## Instrumentation

On the game machine:

```sh
python -m bot.worker run --experiment YOUR_EXPERIMENT --propose --generations 8
```

Worker/orchestrator telemetry goes to `dashboard_status` through a bounded background
queue. Monitoring failures do not block gameplay or strategy generation. Updates from
older workers may include historical records without fresh telemetry or console output.

## Endpoints and checks

The server binds to `127.0.0.1`; it is a local service without public authentication.

- `GET /api/snapshot`: combined live dashboard data. An explicit `experiment` query
  remains supported for integrations; the dashboard never needs it.
- `GET /api/history?offset=25`: the next 25 saved evaluations across all bot experiments.
- `GET /api/frame?experiment=ID&worker=IDENTITY&job=JOB_ID`: current worker JPEG.
- `GET /api/video?experiment=ID&job=JOB_ID`: a saved GridFS recording.
- `GET /api/control/status`: local process state and bounded console tail.

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check dashboard/app.js
npm install --prefix /tmp/gulagloop-dashboard-tests --no-save jsdom
NODE_PATH=/tmp/gulagloop-dashboard-tests/node_modules node --test tests/dashboard_ui.cjs
```

Regression checks cover combined history, isolation of parents/scores/video identities,
late frames, pause/resume, pagination, keyboard interaction, arrow geometry and safe
rendering. A Windows worker + Atlas + Luminal run is still needed to verify gameplay.
