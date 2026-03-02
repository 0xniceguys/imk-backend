from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MK4TaskSpec:
    name: str
    description: str
    observation_mode: str
    action_space: str


DEFAULT_LOWLEVEL_TASK = MK4TaskSpec(
    name="mk4_lowlevel",
    description="Low-level control experiments for deterministic rollouts and future RL.",
    observation_mode="frame",
    action_space="controller_raw",
)
