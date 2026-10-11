import json
from threading import Lock
from types import SimpleNamespace
import unittest
from luminal_client import LuminalClient, enforce_persona
from strategy import BASELINE, Proposal

class ReliabilityTests(unittest.TestCase):
    def client(self, create):
        client = LuminalClient.__new__(LuminalClient)
        client.model = 'mock-model'
        client.cache = {}
        client.cache_lock = Lock()
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return client

    def test_retry_prompt_hash_and_success_cache(self):
        count = 0
        def create(**kwargs):
            nonlocal count
            count += 1
            params = BASELINE.model_dump()
            params['aim_gain'] = 0.72
            text = 'bad' if count == 1 else json.dumps(dict(strategy=params, rationale='Reduce overshoot.'))
            return SimpleNamespace(model='mock-model', usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        client = self.client(create)
        result = client.generate(BASELINE, {}, persona='Tuner')
        logs = result['llm_calls']
        self.assertFalse(result['strategy']['fallback'])
        self.assertNotEqual(logs[0]['prompt_hash'], logs[1]['prompt_hash'])
        self.assertEqual(logs[1]['strategy_id'], result['strategy']['_id'])
        self.assertIn('Tuner', client.cache)

    def test_valid_and_invalid_cached_fallback(self):
        def fail(**kwargs):
            raise TimeoutError('simulated')
        client = self.client(fail)
        params = BASELINE.model_dump()
        params['aim_gain'] = 0.72
        client.cache['Tuner'] = Proposal(strategy=params, rationale='Previously successful proposal.')
        result = client.generate(BASELINE, {}, persona='Tuner')
        self.assertEqual(result['strategy']['fallback_source'], 'cached_proposal')
        params['aim_gain'] = 0.2
        client.cache['Tuner'] = Proposal(strategy=params, rationale='Invalid for current parent.')
        result = client.generate(BASELINE, {}, persona='Tuner')
        self.assertEqual(result['strategy']['fallback_source'], 'local_mutation')

    def test_noop_and_bold_change_count(self):
        for persona in ['Bold', 'Tuner', 'Defender', 'Explorer']:
            with self.assertRaises(ValueError):
                enforce_persona(BASELINE, BASELINE, persona)
        params = BASELINE.model_dump()
        params['aim_gain'] = 0.5
        with self.assertRaises(ValueError):
            enforce_persona(BASELINE, BASELINE.model_validate(params), 'Bold')

if __name__ == '__main__':
    unittest.main()
