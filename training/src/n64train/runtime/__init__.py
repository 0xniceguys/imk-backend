"""Runtime process control and low-level interfaces for emulator integration."""

from n64train.runtime.budget import ExperimentBudget, FrameCategory
from n64train.runtime.bridge import SocketEmulatorBridge
from n64train.runtime.features import FeatureRegistry, FeatureSpec, PrivilegeLevel
from n64train.runtime.types import (
    ActionPacket,
    DifficultySpec,
    MatchSetupSpec,
    ObservationBundle,
    ResetSpec,
    RewardTerms,
    ScenarioSpec,
    SpeedMode,
    StepResult,
    TracedState,
)

__all__ = [
    "ActionPacket",
    "DifficultySpec",
    "ExperimentBudget",
    "FeatureRegistry",
    "FeatureSpec",
    "FrameCategory",
    "MatchSetupSpec",
    "ObservationBundle",
    "PrivilegeLevel",
    "ResetSpec",
    "RewardTerms",
    "ScenarioSpec",
    "SocketEmulatorBridge",
    "SpeedMode",
    "StepResult",
    "TracedState",
]
