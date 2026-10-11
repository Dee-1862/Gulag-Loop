"""File-friendly generation contract; parents must already be ranked by the orchestrator."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator
from luminal_client import PERSONAS
from strategy import Strategy

class ParentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    strategy_id: str = Field(min_length=1)
    params: Strategy
    diagnostics: dict[str, Any]
    mean_fitness: float | None = Field(default=None, allow_inf_nan=False)
    evaluation_count: int = Field(ge=1, strict=True)

class GenerationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: int = Field(ge=1, strict=True)
    parents: list[ParentInput] = Field(min_length=1, max_length=2)
    history: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_context(self):
        ids = [p.strategy_id for p in self.parents]
        if len(set(ids)) != len(ids):
            raise ValueError('Parent strategy IDs must be unique')
        json.dumps(self.model_dump(), allow_nan=False)
        return self

    def assignments(self):
        # Keep conservative personas on champion; exploratory ones use runner-up.
        second = self.parents[-1]
        return {name: (second if name in ('Bold', 'Explorer') else self.parents[0])
                for name in PERSONAS}

def generate_generation(client, request):
    request = request if isinstance(request, GenerationInput) else GenerationInput.model_validate(request)
    references = [p.model_dump() for p in request.parents]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(
            client.generate, parent=parent.params.model_copy(deep=True),
            diagnostics=deepcopy(parent.diagnostics), persona=persona,
            generation=request.generation, parent_id=parent.strategy_id,
            fitness=parent.mean_fitness, history=deepcopy(request.history),
            reference_parents=deepcopy(references),
        ) for persona, parent in request.assignments().items()]
        results = [future.result() for future in futures]
    children = [r['strategy'] for r in results]
    return dict(generation=request.generation, strategies=children,
                llm_calls=[call for r in results for call in r['llm_calls']],
                latency_ms=round((time.monotonic() - started) * 1000),
                fallback_count=sum(p['fallback'] for p in children))
