from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from uuid import uuid4
from evolution import initial_state, pending_jobs, ingest_results, next_request, advance, ranked_parents, save_state, load_state
from luminal_client import mutate

class MockClient:
    def generate(self, parent, persona, parent_id, generation, **kwargs):
        return dict(strategy=dict(_id=str(uuid4()), params=mutate(parent, persona).model_dump(),
            generation=generation, persona=persona, model=None, parent_ids=[parent_id],
            rationale='Offline mock; not Luminal.', fallback=True), llm_calls=[])

def results_for(state, points=100):
    return [dict(job_id=j['job_id'], strategy_id=j['strategy_id'], worker='test-worker',
        map='kino', points=points, survival_s=100.0,
        diagnostics={'overshoot_rate': 0.5, 'last_5s_summary': 'synthetic'}) for j in pending_jobs(state)]

class EvolutionTests(unittest.TestCase):
    def test_multi_generation_and_champion_average(self):
        state = initial_state()
        with self.assertRaises(ValueError):
            next_request(state)
        state = ingest_results(state, results_for(state))
        self.assertEqual(ranked_parents(state)[0]['mean_fitness'], 600)
        for generation in range(1, 4):
            state = advance(state, MockClient())
            self.assertEqual(state['generation'], generation)
            self.assertEqual(len(pending_jobs(state)), 5)
            self.assertEqual(sum(j['kind'] == 'champion_reevaluation' for j in pending_jobs(state)), 1)
            self.assertEqual(len(state['strategies']), 1 + generation * 4)
            jobs = pending_jobs(state)
            docs = results_for(state, points=200)
            champion_job = next(j for j in jobs if j['kind'] == 'champion_reevaluation')
            state = ingest_results(state, docs)
            champion_runs = [r for r in state['runs'] if r['strategy_id'] == champion_job['strategy_id']]
            if generation == 1:
                self.assertEqual(len(champion_runs), 3)
                self.assertAlmostEqual(sum(r['fitness'] for r in champion_runs) / 3, (600 + 600 + 700) / 3)
            self.assertGreaterEqual(len(champion_runs), 2)
            self.assertEqual(len(next_request(state).parents), 2)

    def test_import_atomicity_idempotency_and_environment(self):
        original = initial_state()
        docs = results_for(original)
        state = ingest_results(original, docs)
        self.assertEqual(ingest_results(state, docs), state)
        self.assertEqual(len(original['runs']), 0)
        bad = deepcopy(docs)
        bad[1]['points'] = 101
        with self.assertRaises(ValueError):
            ingest_results(state, bad)
        bad = deepcopy(docs)
        bad[1]['worker'] = 'different-worker'
        with self.assertRaises(ValueError):
            ingest_results(original, bad)
        bad = deepcopy(docs)
        bad[1]['job_id'] = 'unknown'
        with self.assertRaises(ValueError):
            ingest_results(original, bad)
        self.assertEqual(len(original['runs']), 0)

    def test_state_roundtrip(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            state = initial_state()
            save_state(path, state, create=True)
            self.assertEqual(load_state(path), state)
            with self.assertRaises(FileExistsError):
                save_state(path, state, create=True)
            updated = ingest_results(state, results_for(state))
            save_state(path, updated)
            self.assertEqual(load_state(path), updated)

if __name__ == '__main__':
    unittest.main()
