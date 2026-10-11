"""Local evolution state machine. Game workers and MongoDB are external adapters."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import os
from statistics import mean
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from generation import GenerationInput, generate_generation
from strategy import BASELINE, Strategy

class RunResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    worker: str = Field(min_length=1)
    map: str = Field(min_length=1)
    points: int = Field(ge=0, strict=True)
    survival_s: float = Field(ge=0, le=180, allow_inf_nan=False)
    diagnostics: dict[str, Any]

    @property
    def fitness(self):
        return self.points + 5 * self.survival_s

def make_job(strategy_id, generation, kind):
    return dict(job_id=str(uuid4()), strategy_id=strategy_id,
                generation=generation, kind=kind, status='queued')

def initial_state():
    return dict(version=1, generation=0, environment=None,
                strategies=[dict(_id='baseline', generation=0, persona='Baseline',
                    model=None, parent_ids=[], params=BASELINE.model_dump(),
                    rationale='Hand-written baseline.', fallback=False)],
                runs=[], llm_calls=[], history=[],
                jobs=[make_job('baseline', 0, 'baseline') for _ in range(2)])

def ingest_results(state, documents):
    """Validate the whole import before changing state; replay is idempotent."""
    updated = deepcopy(state)
    jobs = {job['job_id']: job for job in updated['jobs']}
    existing = {run['job_id']: run for run in updated['runs']}
    results = [RunResult.model_validate(doc) for doc in documents]
    json.dumps([r.model_dump() for r in results], allow_nan=False)
    for result in results:
        doc = result.model_dump()
        job = jobs.get(result.job_id)
        if job is None or job['strategy_id'] != result.strategy_id:
            raise ValueError('Result must match an issued job and strategy ID')
        if result.job_id in existing:
            old = {k: existing[result.job_id][k] for k in doc}
            if old != doc:
                raise ValueError('Conflicting result for an already completed job')
            continue
        environment = dict(worker=result.worker, map=result.map)
        if updated['environment'] not in (None, environment):
            raise ValueError('Local rankings require the same worker and map; use separate state files for other environments')
        updated['environment'] = environment
        doc['fitness'] = result.fitness
        doc['completed_at'] = datetime.now(timezone.utc).isoformat()
        updated['runs'].append(doc)
        existing[result.job_id] = doc
        job['status'] = 'done'
    return updated

def ranked_parents(state):
    candidates = []
    for strategy in state['strategies']:
        runs = [run for run in state['runs'] if run['strategy_id'] == strategy['_id']]
        if not runs:
            continue
        keys = set.intersection(*(set(r['diagnostics']) for r in runs))
        diagnostics = {}
        for key in keys:
            values = [r['diagnostics'][key] for r in runs]
            if all(type(v) in (int, float) for v in values):
                diagnostics[key] = mean(values)
            else:
                diagnostics[key] = values[-1]
        candidates.append(dict(strategy_id=strategy['_id'], params=strategy['params'],
            diagnostics=diagnostics, mean_fitness=mean(r['fitness'] for r in runs),
            evaluation_count=len(runs)))
    return sorted(candidates, key=lambda p: (-p['mean_fitness'], p['strategy_id']))[:2]

def next_request(state):
    if any(job['status'] != 'done' for job in state['jobs']):
        raise ValueError('Complete all queued evaluations before proposing the next generation')
    parents = ranked_parents(state)
    if not parents:
        raise ValueError('Baseline evaluations are required')
    return GenerationInput(generation=state['generation'] + 1, parents=parents, history=state['history'])

def advance(state, client):
    request = next_request(state)
    batch = generate_generation(client, request)
    updated = deepcopy(state)
    parents = request.parents
    updated['history'].append(dict(generation=state['generation'],
        champion_id=parents[0].strategy_id, best_mean_fitness=parents[0].mean_fitness,
        evaluation_count=parents[0].evaluation_count))
    updated['generation'] = request.generation
    updated['strategies'].extend(batch['strategies'])
    updated['llm_calls'].extend(batch['llm_calls'])
    updated['jobs'].extend([make_job(child['_id'], request.generation, 'child') for child in batch['strategies']])
    updated['jobs'].append(make_job(parents[0].strategy_id, request.generation, 'champion_reevaluation'))
    return updated

def pending_jobs(state):
    strategies = {s['_id']: s for s in state['strategies']}
    return [dict(**job, params=strategies[job['strategy_id']]['params'])
            for job in state['jobs'] if job['status'] == 'queued']

def load_state(path):
    state = json.loads(Path(path).read_text())
    if state.get('version') != 1:
        raise ValueError('Unsupported state version')
    for strategy in state['strategies']:
        Strategy.model_validate(strategy['params'])
    json.dumps(state, allow_nan=False)
    return state

def save_state(path, state, create=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, indent=2, allow_nan=False) + '\n'
    if create:
        with path.open('x') as output:
            output.write(text)
        return
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
            temporary = output.name
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()
