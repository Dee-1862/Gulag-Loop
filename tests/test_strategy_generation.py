import json
import unittest
from types import SimpleNamespace
from pydantic import ValidationError
from strategy import BASELINE, Strategy
from luminal_client import LuminalClient, parse_proposal, enforce_persona, mutate

class GenerationTests(unittest.TestCase):
    def test_bounds_and_bad_types(self):
        params = BASELINE.model_dump()
        params['aim_gain'] = 99.0
        self.assertEqual(Strategy.model_validate(params).aim_gain, 2.0)
        for bad in [True, '0.5', float('nan')]:
            params['aim_gain'] = bad
            with self.assertRaises(ValidationError):
                Strategy.model_validate(params)
        params = BASELINE.model_dump()
        del params['target_rule']
        with self.assertRaises(ValidationError):
            Strategy.model_validate(params)

    def test_fences_and_constraints(self):
        body = json.dumps(dict(strategy=BASELINE.model_dump(), rationale='Keep baseline.'))
        self.assertEqual(parse_proposal('```json\n' + body + '\n```').strategy, BASELINE)
        params = BASELINE.model_dump()
        params['aim_gain'] = 0.4
        with self.assertRaises(ValueError):
            enforce_persona(BASELINE, Strategy.model_validate(params), 'Tuner')
        with self.assertRaises(ValueError):
            enforce_persona(BASELINE, Strategy.model_validate(params), 'Defender')

    def test_fallback_persona_constraints(self):
        for persona in ['Bold', 'Tuner', 'Defender', 'Explorer']:
            for _ in range(20):
                child = mutate(BASELINE, persona)
                enforce_persona(BASELINE, child, persona)
                self.assertNotEqual(child, BASELINE)

    def test_invalid_response_falls_back_and_logs(self):
        client = LuminalClient.__new__(LuminalClient)
        client.model = 'test-model'
        response = SimpleNamespace(model='test-model', usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content='invalid JSON'))])
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
        result = client.generate(BASELINE, {}, persona='Tuner')
        self.assertTrue(result['strategy']['fallback'])
        self.assertIsNone(result['strategy']['model'])
        self.assertEqual(len(result['llm_calls']), 2)
        self.assertTrue(all(not x['ok'] for x in result['llm_calls']))

if __name__ == '__main__':
    unittest.main()
