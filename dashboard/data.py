"""Read-only, experiment-scoped snapshots. Never return credentials or raw errors."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean

PERSONAS = ('Bold', 'Tuner', 'Defender', 'Explorer')

def iso(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
    return str(value) if value is not None else None

def age(value, now):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return max(0, (now - value.replace(tzinfo=value.tzinfo or timezone.utc)).total_seconds())

def clean(doc):
    if isinstance(doc, dict):
        return {k: clean(v) for k, v in doc.items() if k != 'frame'}
    if isinstance(doc, list):
        return [clean(v) for v in doc]
    if isinstance(doc, datetime):
        return iso(doc)
    if doc is None or isinstance(doc, (str, float, int, bool)):
        return doc
    return str(doc)

def assemble(experiment, jobs, runs, calls, playbooks, signals, counts, now=None):
    now = now or datetime.now(timezone.utc)
    pb = {str(p['_id']): p for p in playbooks}
    statuses = Counter(j.get('status', 'unknown') for j in jobs)
    worker_signals = [s for s in signals if s.get('component') == 'vision']
    worker_signals.sort(key=lambda s: age(s.get('updated_at'), now) if age(s.get('updated_at'), now) is not None else float('inf'))
    workers = []
    for signal in worker_signals:
        seconds = age(signal.get('updated_at'), now)
        item = clean(signal)
        item['age_s'] = seconds
        item['state'] = signal.get('state', 'unknown')
        if item['state'] == 'running' and (seconds is None or seconds >= 12):
            item['state'] = 'stale'
        workers.append(item)
    agents = []
    for persona in PERSONAS:
        signal = next((s for s in signals if s.get('component') == 'luminal' and s.get('persona') == persona), None)
        latest_call = next((c for c in calls if c.get('persona') == persona), None)
        latest_pb = next((p for p in playbooks if p.get('persona') == persona), None)
        latest = latest_pb or latest_call or {}
        if latest_call and latest_call.get('generation', 0) > latest.get('generation', 0):
            latest = latest_call
        state = 'waiting'
        if latest is latest_pb:
            state = 'fallback' if latest_pb.get('fallback') else 'complete'
        elif latest is latest_call:
            state = 'complete' if latest_call.get('ok') else 'error'
        signal_time = age(signal.get('updated_at'), now) if signal else None
        # Ignore old progress documents when a newer saved generation exists.
        saved_age = age(latest.get('created_at'), now)
        if signal and (signal.get('generation', 0) > latest.get('generation', 0) or
                       (signal.get('generation', 0) == latest.get('generation', 0) and
                        (saved_age is None or (signal_time is not None and signal_time <= saved_age)))):
            latest = signal
            state = signal.get('state', state)
            if state in ('running', 'retrying') and (signal_time is None or signal_time > 240):
                state = 'stale'
        agents.append(dict(persona=persona, state=state, generation=latest.get('generation'),
            attempt=latest.get('attempt', (latest_call or {}).get('attempt')), latency_ms=(latest_call or {}).get('latency_ms'),
            tokens=((latest_call or {}).get('tokens') or {}).get('total_tokens'),
            rationale=(latest_pb or {}).get('rationale'), changed_params=(latest_pb or {}).get('changed_params', []),
            fallback_source=(latest_pb or {}).get('fallback_source'),
            updated_at=iso(latest.get('updated_at', latest.get('created_at')))))
    groups = defaultdict(list)
    for r in runs:
        groups[r.get('strategy_id')].append(r.get('fitness', 0))
    leaderboard = [dict(strategy_id=k, mean_fitness=round(mean(v), 1), evaluations=len(v),
                        persona=pb.get(str(k), {}).get('persona', 'Baseline'),
                        generation=pb.get(str(k), {}).get('generation', 0)) for k, v in groups.items()]
    leaderboard.sort(key=lambda r: -r['mean_fitness'])
    by_gen = defaultdict(list)
    for r in runs:
        by_gen[r.get('generation', 0)].append(r.get('fitness', 0))
    chart = [dict(generation=g, best=max(v), mean=round(mean(v), 1)) for g, v in sorted(by_gen.items())]
    events = []
    for r in runs[:15]:
        events.append(dict(at=iso(r.get('completed_at')), kind='run', title='Evaluation saved',
            detail=f"{r.get('worker', 'Worker')} · {r.get('points', 0)} points · {r.get('survival_s', 0)}s", collection='runs', id=str(r.get('_id', r.get('job_id')))))
    for c in calls[:15]:
        events.append(dict(at=iso(c.get('created_at')), kind='llm', title=f"{c.get('persona')} · {'validated' if c.get('ok') else 'attempt failed'}",
            detail=f"Generation {c.get('generation')} · {c.get('latency_ms', 0)} ms", collection='llm_calls', id=str(c.get('_id', c.get('strategy_id')))))
    for j in jobs[:15]:
        events.append(dict(at=iso(j.get('completed_at') or j.get('claimed_at') or j.get('created_at')), kind='job',
            title=f"Job {j.get('status', 'queued')}", detail=f"{j.get('kind', 'evaluation')} · {j.get('worker') or 'awaiting worker'}", collection='jobs', id=str(j.get('_id',j.get('job_id')))))
    events.sort(key=lambda e: e['at'] or '', reverse=True)
    latest = runs[0] if runs else {}
    return clean(dict(mode='live', connected=True, experiment=dict(id=str(experiment['_id']), map=experiment.get('map', '—'),
        generation=experiment.get('generation', 0)), updated_at=now.isoformat(), jobs=dict(statuses), counts=counts,
        workers=workers, agents=agents, chart=chart, leaderboard=leaderboard[:6], events=events[:25],
        latest_run=latest, runs_sampled=len(runs), success_count=sum(bool(c.get('ok')) for c in calls),
        call_count_sampled=len(calls), model=next((c.get('model') for c in calls if c.get('model')), None)))

class MongoSource:
    def __init__(self, uri, db_name):
        from pymongo import MongoClient
        self.client = MongoClient(uri, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000, socketTimeoutMS=3000)
        self.db = self.client[db_name]

    def experiments(self):
        return [dict(id=str(d['_id']), map=d.get('map', ''), generation=d.get('generation', 0))
                for d in self.db.experiments.find({'design': 'bot'}, {'map': 1, 'generation': 1}).sort('started_at', -1).limit(50)]

    def snapshot(self, experiment_id):
        from pymongo import timeout
        with timeout(6):
            exp = self.db.experiments.find_one({'_id': experiment_id}, {'map': 1, 'generation': 1})
            if not exp:
                raise LookupError('Experiment not found')
            q = {'experiment_id': experiment_id}
            # Explicit projections prevent unrelated fields/credentials from reaching the browser.
            jobs = list(self.db.jobs.find(q, {k:1 for k in ('job_id','strategy_id','generation','kind','status','worker','created_at','claimed_at','completed_at')}).sort('created_at', -1).limit(500))
            runs = list(self.db.runs.find(q, {k:1 for k in ('job_id','strategy_id','generation','worker','points','survival_s','fitness','diagnostics','completed_at')}).sort('completed_at', -1).limit(500))
            calls = list(self.db.llm_calls.find(q, {k:1 for k in ('persona','model','generation','strategy_id','ok','attempt','tokens','latency_ms','created_at')}).sort('created_at', -1).limit(200))
            playbooks = list(self.db.playbooks.find(q, {k:1 for k in ('persona','generation','rationale','changed_params','fallback','fallback_source','created_at')}).sort('generation', -1).limit(500))
            signals = list(self.db.dashboard_status.find(q, {k:1 for k in ('component','identity','worker','state','persona','generation','attempt','updated_at','fps','detections','action','points','elapsed_s','job_id')}).sort('updated_at', -1).limit(50))
            counts = {c: self.db[c].count_documents(q) for c in ('jobs','runs','playbooks','llm_calls')}
            result = assemble(exp, jobs, runs, calls, playbooks, signals, counts)
            result['jobs'] = {r['_id']: r['count'] for r in self.db.jobs.aggregate([
                {'$match': q}, {'$group': {'_id': '$status', 'count': {'$sum': 1}}}])}
            return result

    def frame(self, experiment_id, identity='', job_id=''):
        import base64
        if not identity:
            return None
        query = {'experiment_id': experiment_id, 'component': 'vision', 'identity': identity,
                 'state': 'running', 'frame': {'$exists': True}}
        if job_id:
            query['job_id'] = job_id
        doc = self.db.dashboard_status.find_one(query, {'frame':1,'updated_at':1}, sort=[('updated_at', -1)])
        seconds = age(doc.get('updated_at'), datetime.now(timezone.utc)) if doc else None
        if seconds is not None and seconds < 12:
            return base64.b64decode(doc['frame'], validate=True)
        return None
