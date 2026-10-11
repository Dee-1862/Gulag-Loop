# Luminal teammate handoff

The base Luminal implementation is ready for integration on `jeffrey`. It accepts
evaluated parent strategies and telemetry, runs four persona agents concurrently,
and returns validated child strategies plus API call logs. Gameplay improvement
has not been demonstrated yet; live tests used synthetic telemetry.

Integrate the existing contracts first. Prompt tuning, richer diagnostics, model
comparisons, and additional optimization can wait until one real generation works.

## Responsibilities

| Owner | Supplies / handles |
| --- | --- |
| Jeffrey / Luminal | Strategy schema, prompts, model calls, proposal validation, retries, fallbacks, child lineage, call logs |
| Vision / controller teammate | Capture, detection, control, safe abort, reset, points gained, survival, run diagnostics, execution of supplied strategy parameters |
| MongoDB / orchestration teammate | Persistence, separate evaluation jobs, atomic job claims, completed-run aggregation, top-two parent selection, champion re-evaluation, production generation coordination |

`evolution.py` is an optional local reference implementation for ranking and job
coordination. MongoDB can replace its storage; do not run the local scheduler and
MongoDB scheduler independently against the same experiment.

## Setup

Run from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python offline_demo.py
```

Create a private `.env` using `.env.example` as a reference:

```env
LUMINAL_API_KEY=YOUR_PRIVATE_KEY
LUMINAL_BASE_URL=https://flash.luminal.cloud/v1
LUMINAL_MODEL=deepseek-ai/DeepSeek-V4.1-Flash
```

Keys and `.venv` are ignored by Git. Each laptop creates its own environment.
Only the machine making model calls needs the key. JSON mode is opt-in; the
working default asks for JSON in the prompt.

## Recommended Python entry point

```python
from generation import generate_generation
from luminal_client import LuminalClient

client = LuminalClient()  # Load .env; reuse this client across generations.
batch = generate_generation(client, generation_input)
# Database adapter persists batch['strategies'] and batch['llm_calls'].
```

Run from the repository root so default `.env` discovery resolves correctly.
`generate_generation` accepts the following JSON-compatible dictionary:

| Field | Contract |
| --- | --- |
| `generation` | Integer >= 1; generation being proposed |
| `parents` | One or two parents, already ranked with champion first |
| `history` | Optional list of score-history dictionaries |

Each parent requires:

| Field | Contract |
| --- | --- |
| `strategy_id` | Nonempty unique string; convert MongoDB ObjectId to a string if needed |
| `params` | Complete 12-field strategy from `strategy.py` |
| `diagnostics` | JSON-compatible dictionary; omit unavailable metrics |
| `evaluation_count` | Integer >= 1; number of runs represented |
| `mean_fitness` | Optional finite number; average across evaluations |

Use [examples/generation_input.json](../examples/generation_input.json) for a complete
copyable input. Those values are synthetic. Unknown parent/request fields are
rejected, so adapt MongoDB documents rather than passing entire documents directly.

With two parents, Tuner and Defender mutate the champion; Bold and Explorer mutate
the runner-up. All agents see both parents and their evaluation counts. With one
parent all four mutate it. `parent_ids` records the actual mutation parent, not
both strategies supplied as context.

## Controller strategy contract

The controller consumes a child's `params`, not its metadata.

| Parameter | Type | Allowed values |
| --- | --- | --- |
| `det_conf` | float | 0.15–0.70 |
| `aim_gain` | float | 0.1–2.0 |
| `aim_max_step` | integer | 5–200 |
| `aim_y_offset` | float | 0.0–0.5 |
| `fire_radius_px` | integer | 5–80 |
| `burst_ms` | integer | 30–400 |
| `retreat_box_h` | integer | 50–500 |
| `strafe_mode` | string | none, left, right, alternate |
| `strafe_period_s` | float | 0.5–5.0 |
| `reload_when_clear_at` | integer | 0–8 |
| `scan_turn_px` | integer | 0–300 |
| `target_rule` | string | nearest, largest, center |

Numeric values are clamped to bounds. Missing fields, extra fields, invalid enums,
nonfinite numbers, and wrong numeric types are rejected. The controller should use
the same schema rather than silently assigning different meanings or units.

Start with `strategy.BASELINE`. A smaller `retreat_box_h` means earlier retreat.
`reload_when_clear_at=0` disables proactive reloading. Frame-coordinate parameters
assume the resolution/capture coordinates in the build plan; agree on these before
comparing strategies.

## Telemetry conventions

Prefer these names from the plan:

- `overshoot_rate`: fraction 0–1; target-error sign flips, potentially confounded by target changes.
- `avg_frames_to_lock`: frames until crosshair is within firing radius.
- `time_no_target_pct`: percentage 0–100, not a fraction.
- `close_calls`: frames with very close zombies, not distinct encounters.
- `retreat_frames`: frames retreating; counts should use the same sampling rate as close calls.
- `shots_without_target`, `hits_proxy`, `det_count_avg`, `stuck_events`: diagnostic counts or averages as defined in the plan.
- `death_time_s`, `last_5s_summary`: timing and brief narrative context.

Diagnostics may be incomplete during first integration. The agent is instructed
not to invent missing metrics. Scores alone allow calls to run but give poor tuning
signals. Treat OCR hit proxies and visual stuck detection as estimates.

## Output and persistence

`batch` contains:

- `generation`, total `latency_ms`, and `fallback_count`.
- `strategies`: four children, in Bold/Tuner/Defender/Explorer order.
- `llm_calls`: flattened per-persona attempt logs, potentially more than four.

Each child includes `_id` (UUID string), `generation`, `persona`, `model`,
`parent_ids`, `params`, `rationale`, `created_at`, `status="queued"`, `fallback`,
`fallback_source`, and `changed_params`.

Each attempt log includes `strategy_id`, `generation`, `persona`, `model`,
`created_at`, `attempt`, `prompt_hash`, `latency_ms`, and `ok`. Successful responses
include `tokens` when supplied by the provider and `clamped_params` if parsing
succeeded. Failed attempts include `error_type` and possibly `http_status`.
Tokens is a usage dictionary, not just an integer. Credentials and raw API error
bodies are excluded.

Keep UUIDs or map them consistently; logs link to the returned child UUID.
Timestamps are UTC ISO strings; convert them to BSON datetimes in the adapter if
preferred. Retries are attempts for one child, not additional strategies.

Fallback children remain valid and executable, but mark them clearly in analysis:
`cached_proposal` reuses a previously successful, revalidated proposal;
`local_mutation` is locally generated. A fallback's `model` is null. Failed calls
must not be presented as successful model usage.

## Separate jobs from strategies

A strategy can have many evaluations. Queue evaluation jobs separately from the
strategy document; otherwise a single strategy-level status cannot represent two
baseline runs or recurring champion re-evaluations.

The local reference issues jobs with `job_id`, `strategy_id`, `generation`, `kind`,
and `status`; `pending_jobs()` additionally includes controller-ready `params`.
Run results must preserve both IDs. A result has points **gained** and survival
seconds, with fitness computed as `points + 5 × survival_s`.

For local imports, a results JSON array contains:

```json
[{
  "job_id": "ACTUAL_ISSUED_JOB_ID",
  "strategy_id": "baseline",
  "worker": "laptop-1",
  "map": "kino",
  "points": 200,
  "survival_s": 112.0,
  "diagnostics": {"overshoot_rate": 0.58, "time_no_target_pct": 37}
}]
```

Survival is bounded to 180 seconds. Unknown job IDs, strategy mismatches, conflicting
replays, and invalid results are rejected. The local scheduler compares one worker
and map per state file to avoid unnormalized hardware/map differences. Production
multi-worker normalization is a separate orchestration decision.

## First integration acceptance check

1. Execute the baseline twice and record points gained, survival, and available diagnostics.
2. Build one-parent `generation_input` with baseline mean fitness and evaluation count 2.
3. Call `generate_generation`; confirm four validated children and linked call logs.
4. Persist each child and queue four child jobs plus one baseline champion re-evaluation.
5. Controller executes those five jobs and returns results linked to the issued IDs.
6. Aggregate all evaluations, retain the top two, and generate the next generation.

Verify parameter units, IDs, diagnostics, and repeat evaluation support first.
A successful integration means that this round trip completes; demonstrating
improvement requires additional controlled gameplay evaluations.

## File-based alternative

Validate the example without networking:

```sh
.venv/bin/python generate_strategies.py --input examples/generation_input.json --dry-run
```

Generate proposals from a real handoff file (calls Luminal):

```sh
.venv/bin/python generate_strategies.py --input real-generation-input.json --output outputs/generation-1.json
```

Use `evolve_local.py` and the README's local-loop commands if MongoDB is not ready.
Local state is resumable but supports one writer at a time. Runtime artifacts under
`outputs/` stay local by default. Review and integrate through `jeffrey`; `master`
requires team approval. Since master reverted the original implementation commit,
a future PR may need to reapply that reverted content explicitly; a plain merge of
only subsequent commits would omit the original files. Handle this after approval.
