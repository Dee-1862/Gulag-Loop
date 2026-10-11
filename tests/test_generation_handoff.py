import json
from pathlib import Path
from threading import Barrier
import unittest
from pydantic import ValidationError
from generation import GenerationInput, generate_generation

class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(Path('examples/generation_input.json').read_text())

    def test_two_parents_parallel_context_and_lineage(self):
        barrier = Barrier(4, timeout=5)
        calls = []
        class Client:
            def generate(self, **kwargs):
                barrier.wait()
                calls.append(kwargs)
                return dict(strategy=dict(persona=kwargs['persona'], parent_ids=[kwargs['parent_id']], fallback=False), llm_calls=[])
        batch = generate_generation(Client(), self.data)
        self.assertEqual(len(batch['strategies']), 4)
        for call in calls:
            self.assertEqual(len(call['reference_parents']), 2)
            expected = 'sample-runner-up' if call['persona'] in ('Bold', 'Explorer') else 'sample-champion'
            self.assertEqual(call['parent_id'], expected)
            self.assertEqual(call['generation'], 2)
            self.assertEqual(call['history'], self.data['history'])
        self.assertEqual(batch['fallback_count'], 0)

    def test_one_parent_and_invalid_handoffs(self):
        one = {**self.data, 'parents': self.data['parents'][:1]}
        request = GenerationInput.model_validate(one)
        self.assertEqual({p.strategy_id for p in request.assignments().values()}, {'sample-champion'})
        self.data['parents'][1]['strategy_id'] = 'sample-champion'
        with self.assertRaises(ValidationError):
            GenerationInput.model_validate(self.data)
        one['parents'][0]['evaluation_count'] = 0
        with self.assertRaises(ValidationError):
            GenerationInput.model_validate(one)

if __name__ == '__main__':
    unittest.main()
