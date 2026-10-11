"""Local evolution CLI: init → jobs → ingest → propose → jobs, repeatedly."""
import argparse
import json
from pathlib import Path
from evolution import initial_state, load_state, save_state, ingest_results, next_request, advance, pending_jobs, ranked_parents
from luminal_client import LuminalClient, load_config
from strategy import Proposal

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--env-file', type=Path, default=Path(__file__).parent / '.env')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    sub.add_parser('jobs')
    sub.add_parser('status')
    ingest = sub.add_parser('ingest')
    ingest.add_argument('--results', required=True, type=Path)
    propose = sub.add_parser('propose')
    propose.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.state.resolve() == args.env_file.resolve():
        parser.error('State must not overwrite credentials')
    if args.command == 'init':
        save_state(args.state, initial_state(), create=True)
        print('Initialized two baseline evaluation jobs.')
        return
    state = load_state(args.state)
    if args.command == 'jobs':
        print(json.dumps(pending_jobs(state), indent=2))
    elif args.command == 'status':
        print(json.dumps(dict(generation=state['generation'], pending_jobs=len(pending_jobs(state)),
            top_parents=ranked_parents(state), llm_call_count=len(state['llm_calls'])), indent=2))
    elif args.command == 'ingest':
        state = ingest_results(state, json.loads(args.results.read_text()))
        save_state(args.state, state)
        print('Imported results. Pending evaluations:', len(pending_jobs(state)))
    elif args.command == 'propose':
        request = next_request(state)
        if args.dry_run:
            print(request.model_dump_json(indent=2))
            return
        client = LuminalClient(load_config(args.env_file))
        # Rehydrate last successful responses for per-persona cache fallback.
        for child in state['strategies']:
            if child.get('persona') in ('Bold', 'Tuner', 'Defender', 'Explorer') and not child.get('fallback'):
                client.cache[child['persona']] = Proposal(strategy=child['params'], rationale=child['rationale'])
        state = advance(state, client)
        save_state(args.state, state)
        print('Generation', state['generation'], 'queued. Pending evaluations:', len(pending_jobs(state)))

if __name__ == '__main__':
    main()
