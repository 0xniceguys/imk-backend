from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticPriority:
    name: str
    rationale: str
    phase: str


SEMANTIC_PRIORITIES: tuple[SemanticPriority, ...] = (
    SemanticPriority(
        name="positions_health_timer_facing",
        rationale="Ground spacing, survival, and time-pressure reasoning for all model families.",
        phase="phase0b",
    ),
    SemanticPriority(
        name="hit_block_whiff_events",
        rationale="Teach combat outcome semantics and reward decomposition beyond raw reward.",
        phase="phase0c",
    ),
    SemanticPriority(
        name="action_state_ids",
        rationale="Expose startup/active/recovery timing semantics for high-level tactical planning.",
        phase="phase0d",
    ),
    SemanticPriority(
        name="knockdown_hitstun_blockstun",
        rationale="Support oki/pressure/defense scenario taxonomy and temporal value estimation.",
        phase="phase0d",
    ),
)
