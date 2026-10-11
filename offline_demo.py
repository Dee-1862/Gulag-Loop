"""Exercise three generations with synthetic results and local mutations only.

No Luminal calls, game runs, or MongoDB writes. Output is not improvement evidence.
"""
from uuid import uuid4
from evolution import initial_state, pending_jobs, ingest_results, advance, ranked_parents
from luminal_client import mutate

class OfflineClient:
    def generate(self, parent, persona, parent_id, generation, **kwargs):
        return dict(strategy=dict(_id=str(uuid4()), params=mutate(parent, persona).model_dump(),
            generation=generation, persona=persona, model=None, parent_ids=[parent_id],
            rationale='Synthetic offline demo mutation, not Luminal output.', fallback=True), llm_calls=[])

def main():
    print('SYNTHETIC DEMO: no API calls or real gameplay; scores are fabricated.')
    state = initial_state()
    client = OfflineClient()
    for round_number in range(4):
        results = [dict(job_id=j['job_id'], strategy_id=j['strategy_id'], worker='mock-worker',
                        map='mock-map', points=100, survival_s=100.0,
                        diagnostics={'overshoot_rate': 0.5, 'time_no_target_pct': 30})
                   for j in pending_jobs(state)]
        state = ingest_results(state, results)
        print(f"Generation {state['generation']}: {len(results)} mock runs imported; {len(ranked_parents(state))} parents retained.")
        if round_number < 3:
            state = advance(state, client)
    print('Three generation transitions completed. No claim of gameplay improvement.')

if __name__ == '__main__':
    main()
