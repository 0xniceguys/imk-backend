from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FrameCategory(str, Enum):
    TRAINING = "training"
    SCENARIO_BRANCH = "scenario_branch"
    SELF_PLAY = "self_play"
    EVALUATION = "evaluation"
    FAILED = "failed"


class BudgetExceededError(RuntimeError):
    pass


@dataclass
class ExperimentBudget:
    max_env_frames: int
    counts: dict[FrameCategory, int] = field(
        default_factory=lambda: {category: 0 for category in FrameCategory}
    )

    def total_env_frames(self) -> int:
        return sum(self.counts.values())

    def remaining_env_frames(self) -> int:
        return self.max_env_frames - self.total_env_frames()

    def can_consume(self, frames: int) -> bool:
        if frames < 0:
            return False
        return self.total_env_frames() + frames <= self.max_env_frames

    def record(self, category: FrameCategory, frames: int) -> None:
        if frames < 0:
            raise ValueError("frames must be >= 0")
        if not self.can_consume(frames):
            raise BudgetExceededError(
                f"Frame budget exceeded: tried to add {frames}, remaining {self.remaining_env_frames()}"
            )
        self.counts[category] += frames

    def snapshot(self) -> dict[str, int]:
        data = {category.value: self.counts[category] for category in FrameCategory}
        data["total_env_frames"] = self.total_env_frames()
        data["remaining_env_frames"] = self.remaining_env_frames()
        data["max_env_frames"] = self.max_env_frames
        return data

