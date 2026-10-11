# Luminal strategy generation

Run commands from this directory. Credentials stay in the ignored `.env` file.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python test_luminal.py
.venv/bin/python test_luminal.py --live
```

`--live` sends a billable request to Luminal. The default test is offline.
Copy `.env.example` to `.env` only if you do not already have credentials configured.

## Teammate integration

```python
from luminal_client import LuminalClient
from strategy import Strategy

client = LuminalClient()
result = client.generate(
    parent=Strategy.model_validate(parent_document['params']),
    parent_id=str(parent_document['_id']),
    diagnostics=run_summary['diagnostics'],
    fitness=run_summary['fitness'],
    generation=parent_document['generation'] + 1,
    persona='Tuner',  # Bold, Tuner, Defender, Explorer
    history=[],
)
# MongoDB owner persists result['strategy'] and result['llm_calls'].
# Controller reads result['strategy']['params'].
```

All twelve strategy fields are required. Numeric values are clamped; wrong types,
unknown fields, invalid enums, and nonfinite numbers are rejected. Persona constraints
are enforced after validation. Two proposal attempts are allowed; failed proposals
produce an explicitly marked local mutation fallback. The SDK separately retries
transient request errors twice. Logs exclude credentials and raw error bodies.

This module does not run games, rank champions, or write to MongoDB. Those belong
to the orchestrator. Sample diagnostics are synthetic, not evidence of improvement.
