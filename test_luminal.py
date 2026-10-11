"""Single-agent smoke test: .venv/bin/python test_luminal.py [--live]."""
import argparse
import json
from luminal_client import LuminalClient, enforce_persona, parse_proposal
from strategy import BASELINE

DIAGNOSTICS = {
    'overshoot_rate': 0.58, 'avg_frames_to_lock': 14,
    'close_calls': 41, 'retreat_frames': 3, 'time_no_target_pct': 37,
    'death_time_s': 112,
    'last_5s_summary': 'Two zombies at point-blank range while reloading.',
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Send an authenticated Luminal request')
    args = parser.parse_args()
    if args.live:
        result = LuminalClient().generate(BASELINE, DIAGNOSTICS, fitness=640)
        print(json.dumps(result, indent=2))
        if result['strategy']['fallback']:
            raise SystemExit('Live proposal failed; returned a local fallback. See call error types above.')
    else:
        params = BASELINE.model_dump()
        params['aim_gain'] = 0.72
        proposal = parse_proposal(json.dumps(dict(strategy=params, rationale='Reduce gain to address overshoot.')))
        enforce_persona(BASELINE, proposal.strategy, 'Tuner')
        print('Offline proposal parsing and persona validation passed. Use --live to test Luminal.')

if __name__ == '__main__':
    main()
