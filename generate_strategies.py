"""Generate a batch from a JSON handoff, or validate it without API access."""
import argparse
import json
from pathlib import Path
from generation import GenerationInput, generate_generation
from luminal_client import LuminalClient, load_config

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--env-file', type=Path, default=Path(__file__).parent / '.env')
    parser.add_argument('--dry-run', action='store_true', help='Validate and show parent assignments; no API requests')
    args = parser.parse_args()
    if not args.dry_run and args.output is None:
        parser.error('--output is required for live generation')
    if args.output and args.output.resolve() in {args.input.resolve(), args.env_file.resolve()}:
        parser.error('Output must not overwrite the input or credentials')
    if args.output and args.output.exists():
        parser.error('Output already exists; choose a new filename')
    request = GenerationInput.model_validate(json.loads(args.input.read_text()))
    if args.dry_run:
        print(json.dumps(dict(valid=True, generation=request.generation,
                             assignments={p: x.strategy_id for p, x in request.assignments().items()}), indent=2))
        return
    result = generate_generation(LuminalClient(load_config(args.env_file)), request)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write('\n')
    print(f"Wrote {len(result['strategies'])} strategies to {args.output}; fallbacks: {result['fallback_count']}")

if __name__ == '__main__':
    main()
