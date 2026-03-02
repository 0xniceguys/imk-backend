from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from n64train.runtime.actions import ControllerState, MacroAction


class SpeedMode(str, Enum):
    DEBUG_VISIBLE = "DEBUG_VISIBLE"
    TRAIN_TURBO = "TRAIN_TURBO"
    EVAL_DETERMINISTIC = "EVAL_DETERMINISTIC"


class ScenarioSource(str, Enum):
    HUMAN = "human"
    SCRIPTED = "scripted"
    AGENT = "agent"
    CURATED = "curated"


class TacticalClass(str, Enum):
    NEUTRAL_SPACING = "neutral_spacing"
    CORNER_OFFENSE = "corner_offense"
    CORNER_DEFENSE = "corner_defense"
    WAKEUP_OKI = "wakeup_oki"
    ANTI_AIR = "anti_air"
    PUNISH_WINDOW = "punish_window"
    COMBO_CONTINUATION = "combo_continuation"
    LOW_HEALTH_SCRAMBLE = "low_health_scramble"


@dataclass(frozen=True)
class DifficultySpec:
    use_max_cpu: bool = True
    cpu_level: int | None = None

    def resolved_label(self) -> str:
        if self.use_max_cpu:
            return "MAX_CPU"
        if self.cpu_level is None:
            return "UNSPECIFIED"
        return f"CPU_{self.cpu_level}"


@dataclass(frozen=True)
class MatchSetupSpec:
    player_character_id: int | None = None
    opponent_character_id: int | None = None
    stage_id: int | None = None
    cpu_controls_opponent: bool = True
    difficulty: DifficultySpec = field(default_factory=DifficultySpec)
    notes: str = ""


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    savestate_path: Path
    tactical_class: TacticalClass
    source: ScenarioSource
    matchup: str = ""
    stage: str = ""
    side: str = "P1"
    difficulty_score: float = 1.0
    version: int = 1
    tags: tuple[str, ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "savestate_path": str(self.savestate_path),
            "tactical_class": self.tactical_class.value,
            "source": self.source.value,
            "matchup": self.matchup,
            "stage": self.stage,
            "side": self.side,
            "difficulty_score": self.difficulty_score,
            "version": self.version,
            "tags": list(self.tags),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScenarioSpec":
        return cls(
            scenario_id=str(payload["scenario_id"]),
            savestate_path=Path(str(payload["savestate_path"])),
            tactical_class=TacticalClass(str(payload["tactical_class"])),
            source=ScenarioSource(str(payload["source"])),
            matchup=str(payload.get("matchup", "")),
            stage=str(payload.get("stage", "")),
            side=str(payload.get("side", "P1")),
            difficulty_score=float(payload.get("difficulty_score", 1.0)),
            version=int(payload.get("version", 1)),
            tags=tuple(str(x) for x in payload.get("tags", [])),
            notes=str(payload.get("notes", "")),
        )


@dataclass(frozen=True)
class ResetSpec:
    scenario_id: str | None = None
    savestate_path: Path | None = None
    slot: int | None = None
    episode_seed: int | None = None


@dataclass(frozen=True)
class TracedState:
    frame_id: int
    p1_x: float | None = None
    p2_x: float | None = None
    p1_y: float | None = None
    p2_y: float | None = None
    p1_health: int | None = None
    p2_health: int | None = None
    timer: int | None = None
    p1_facing: int | None = None
    p2_facing: int | None = None
    extras: dict[str, float | int | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class RewardTerms:
    # ── Health / combat ──────────────────────────────────────────────────────
    round_win: float = 0.0
    damage_dealt: float = 0.0
    damage_taken: float = 0.0
    hit_confirm_bonus: float = 0.0
    block_success_bonus: float = 0.0
    whiff_punished_penalty: float = 0.0
    idle_timeout_penalty: float = 0.0
    illegal_state_penalty: float = 0.0
    # ── Episode outcomes ─────────────────────────────────────────────────────
    win_bonus: float = 0.0       # flat reward for winning the round
    loss_penalty: float = 0.0    # flat penalty for losing (should be negative)
    # ── Positioning / spacing ────────────────────────────────────────────────
    approach_reward: float = 0.0  # reward for closing distance toward opponent
    distance_penalty: float = 0.0 # penalty for being too far to attack
    # ── Survival ─────────────────────────────────────────────────────────────
    survival: float = 0.0         # small reward per step alive
    # ── Extras ───────────────────────────────────────────────────────────────
    spam_penalty: float = 0.0       # penalty for repeating same move / attack cooldown violation
    extras: dict[str, float] = field(default_factory=dict)

    def scalar(self) -> float:
        total = (
            self.round_win
            + self.damage_dealt
            + self.damage_taken
            + self.hit_confirm_bonus
            + self.block_success_bonus
            + self.whiff_punished_penalty
            + self.idle_timeout_penalty
            + self.illegal_state_penalty
            + self.win_bonus
            + self.loss_penalty
            + self.approach_reward
            + self.distance_penalty
            + self.survival
            + self.spam_penalty
        )
        total += sum(self.extras.values())
        return total


@dataclass(frozen=True)
class EventLabel:
    name: str
    present: bool
    confidence: float = 1.0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimingKeys:
    run_id: str
    episode_id: str
    emulator_frame_id: int
    action_frame_id: int
    scenario_id: str | None = None


@dataclass(frozen=True)
class ObservationBundle:
    timing: TimingKeys
    traced_state: TracedState | None = None
    frame_shape: tuple[int, int, int] | None = None
    frame_bytes: bytes | None = None
    privileged_features: dict[str, float | int | bool] = field(default_factory=dict)
    meta_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionPacket:
    macro_action: MacroAction | None = None
    micro_controller_state: ControllerState = field(default_factory=ControllerState)
    repeat_frames: int = 1

    def __post_init__(self) -> None:
        if self.repeat_frames < 1:
            raise ValueError("repeat_frames must be >= 1")


@dataclass(frozen=True)
class StepResult:
    observation: ObservationBundle
    reward_terms: RewardTerms = field(default_factory=RewardTerms)
    events: tuple[EventLabel, ...] = ()
    done: bool = False
    truncated: bool = False
    info: dict[str, Any] = field(default_factory=dict)

