"""Validated strategy contract shared with the game controller."""
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

BOUNDS = {
    'det_conf': (0.15, 0.70), 'aim_gain': (0.1, 2.0),
    'aim_max_step': (5, 200), 'aim_y_offset': (0.0, 0.5),
    'fire_radius_px': (5, 80), 'burst_ms': (30, 400),
    'retreat_box_h': (50, 500), 'strafe_period_s': (0.5, 5.0),
    'reload_when_clear_at': (0, 8), 'scan_turn_px': (0, 300),
}

class Strategy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    det_conf: float = Field(ge=0.15, le=0.70)
    aim_gain: float = Field(ge=0.1, le=2.0)
    aim_max_step: int = Field(ge=5, le=200)
    aim_y_offset: float = Field(ge=0, le=0.5)
    fire_radius_px: int = Field(ge=5, le=80)
    burst_ms: int = Field(ge=30, le=400)
    retreat_box_h: int = Field(ge=50, le=500)
    strafe_mode: Literal['none', 'left', 'right', 'alternate']
    strafe_period_s: float = Field(ge=0.5, le=5)
    reload_when_clear_at: int = Field(ge=0, le=8)
    scan_turn_px: int = Field(ge=0, le=300)
    target_rule: Literal['nearest', 'largest', 'center']

    @field_validator(*BOUNDS, mode='before')
    @classmethod
    def clamp_numeric(cls, value, info):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('Expected a JSON number')
        if not math.isfinite(value):
            raise ValueError('Number must be finite')
        lo, hi = BOUNDS[info.field_name]
        return max(lo, min(hi, value))

class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    strategy: Strategy
    rationale: str = Field(min_length=1, max_length=1000)

BASELINE = Strategy(
    det_conf=0.35, aim_gain=0.8, aim_max_step=80, aim_y_offset=0.15,
    fire_radius_px=25, burst_ms=120, retreat_box_h=300,
    strafe_mode='none', strafe_period_s=2.0, reload_when_clear_at=3,
    scan_turn_px=60, target_rule='center',
)
