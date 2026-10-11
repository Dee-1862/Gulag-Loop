# Research basis and experiment

## Question

Does keeping only **validated** lessons make a self-improving agent better than keeping **every** lesson, or none? Xiong et al. (2025) found that LLM agents copy the outputs of retrieved memories, so bad memories spread errors, and that selective adding and deleting of memories helps. We test that finding in a new setting: an agent team evolving a game playbook.

## Predictions (commit this section before running)

- **P1.** Arm C (validated memory) ends with a higher held-out mean than arm A (no memory) and arm B (naive memory).
- **P2.** All three planted bad lessons are retired in arm C within 3 generations of being planted.
- **P3.** Arm B's held-out mean drops in the 2 generations after the planted lessons are inserted.

Pass rules: P1 holds if C's final 95% interval does not overlap A's or B's. Otherwise we report it as "not shown".

## Protocol

- 20 training seeds (used to pick the best proposal) and 20 held-out seeds (only for reporting, different player-skill levels), stored in the `experiments` document.
- Same model, temperature 0.9, 6 proposals per generation, 8 generations, the same hand-written generation-0 playbook in every arm.
- At generation 2, three bad lessons are inserted into arms B and C with `planted: true`.
- Statistics: mean round reached over held-out seeds, 95% bootstrap interval (1,000 resamples). Mean over all generations as a summary of the whole curve.

## Results

See [results/RESULTS.md](../results/RESULTS.md), generated from MongoDB by `scripts/reproduce_chart.py`.

## Memory design and the papers behind it

| Idea | Paper | How we use it |
| --- | --- | --- |
| Working, episodic, semantic and procedural memory | [CoALA](https://arxiv.org/abs/2309.02427), Sumers et al. 2023 | One collection per memory type |
| Retrieval by relevance + recency + importance | [Generative Agents](https://arxiv.org/abs/2304.03442), Park et al. 2023 | `retrieve()` computes it in one aggregation, equal weights as in the paper |
| Case-based memory without fine-tuning | [Memento](https://arxiv.org/abs/2508.16153), 2025 | `episodes`: state, playbook, outcome |
| Skill library / reusable workflows | [Voyager](https://arxiv.org/abs/2305.16291), [Agent Workflow Memory](https://arxiv.org/abs/2409.07429) | `playbooks` with lineage |
| Lessons from failures | [Reflexion](https://arxiv.org/abs/2303.11366), [ExpeL](https://arxiv.org/abs/2308.10144) | `lessons`, one per generation |
| Linked, evolving notes | [A-Mem](https://arxiv.org/abs/2502.12110), NeurIPS 2025 | `situations` tree, walked with `$graphLookup` |
| Bad memories propagate; selective add/delete helps | [Xiong et al. 2025](https://arxiv.org/abs/2505.16067) | Lesson lifecycle: candidate → validated / retired |
| Mutate, evaluate, select | [GEPA](https://github.com/gepa-ai/gepa), [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) | The evolution loop's shape |

## Limitations

- One run per arm; intervals cover seed variation, not variation between LLM runs.
- Simplified simulator, short horizon (8 generations).
- A playbook's gain is credited equally to every lesson it was shown.
