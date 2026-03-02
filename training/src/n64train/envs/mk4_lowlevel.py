from __future__ import annotations

from dataclasses import dataclass
from itertools import count

from n64train.runtime.bridge import EmulatorBridge, UnimplementedBridge
from n64train.runtime.budget import ExperimentBudget, FrameCategory
from n64train.runtime.launcher import LaunchOptions, Mupen64PlusSession
from n64train.runtime.memory import MemoryProbe
from n64train.runtime.observations import ObservationSpec
from n64train.runtime.scenarios import load_scenario
from n64train.runtime.savestates import newest_savestate
from n64train.runtime.types import (
    ActionPacket,
    MatchSetupSpec,
    ObservationBundle,
    ResetSpec,
    RewardTerms,
    ScenarioSpec,
    SpeedMode,
    StepResult,
    TimingKeys,
    TracedState,
)


@dataclass(frozen=True)
class MK4EnvConfig:
    observation: ObservationSpec = ObservationSpec()
    auto_load_latest_state: bool = True
    headless_dummy: bool = False
    headed: bool = True
    speed_mode: SpeedMode = SpeedMode.DEBUG_VISIBLE
    resolution: str = "320x240"
    instance_id: str = "mk4-env"
    max_env_frames: int = 1_000_000
    allow_stub_steps: bool = False
    turbo_dummy_audio: bool = False


class MK4LowLevelEnv:
    """
    Early training environment scaffold.

    Current scope:
    - boot/stop emulator
    - optionally boot with newest savestate

    Future scope:
    - frame capture
    - RAM export
    - action injection
    - reward calculation
    - bridge-backed deterministic frame stepping
    """

    _episode_seq = count(1)

    def __init__(
        self,
        config: MK4EnvConfig | None = None,
        bridge: EmulatorBridge | None = None,
    ) -> None:
        self.config = config or MK4EnvConfig()
        self.bridge = bridge or UnimplementedBridge()
        self.session: Mupen64PlusSession | None = None
        self.match_setup: MatchSetupSpec | None = None
        self.current_scenario: ScenarioSpec | None = None
        self.speed_mode = self.config.speed_mode
        self.budget = ExperimentBudget(max_env_frames=self.config.max_env_frames)
        self._episode_id = "episode-0"
        self._frame_id = 0
        self._run_id = self.config.instance_id

    def launch(self) -> None:
        load_latest = self.config.auto_load_latest_state and newest_savestate() is not None
        self.session = Mupen64PlusSession(
            LaunchOptions(
                headless_dummy=self.config.headless_dummy,
                load_latest_state=load_latest,
                speed_mode=self.speed_mode,
                headed=self.config.headed,
                resolution=self.config.resolution,
                instance_id=self.config.instance_id,
                dummy_audio_in_turbo=self.config.turbo_dummy_audio,
            )
        )
        self.session.start()

    def close(self) -> None:
        if self.session is not None:
            self.session.stop()
        self.bridge.close()

    def configure_match(self, setup_spec: MatchSetupSpec) -> None:
        self.match_setup = setup_spec
        try:
            self.bridge.configure_match(setup_spec)
        except NotImplementedError:
            pass

    def set_speed_mode(self, speed_mode: SpeedMode) -> None:
        self.speed_mode = speed_mode
        try:
            self.bridge.set_speed_mode(speed_mode)
        except NotImplementedError:
            # The shell-based local runner doesn't expose dynamic speed changes yet.
            pass

    def load_scenario(self, scenario_id: str) -> ScenarioSpec:
        spec = load_scenario(scenario_id)
        self.current_scenario = spec
        try:
            self.bridge.load_scenario(spec)
        except NotImplementedError:
            pass
        return spec

    def reset(self, reset_spec: ResetSpec | None = None) -> ObservationBundle:
        if reset_spec and reset_spec.scenario_id:
            self.current_scenario = load_scenario(reset_spec.scenario_id)
        try:
            obs = self.bridge.reset_match(reset_spec)
            self._episode_id = obs.timing.episode_id
            self._frame_id = obs.timing.emulator_frame_id
            return obs
        except NotImplementedError:
            self._episode_id = f"episode-{next(self._episode_seq)}"
            self._frame_id = 0
            return self._placeholder_observation()

    def step(self, action_packet: ActionPacket) -> StepResult:
        try:
            result = self.bridge.step(action_packet)
            frames = action_packet.repeat_frames
            self.budget.record(FrameCategory.TRAINING, frames)
            self._frame_id = result.observation.timing.emulator_frame_id
            self._episode_id = result.observation.timing.episode_id
            return result
        except NotImplementedError:
            if not self.config.allow_stub_steps:
                raise NotImplementedError(
                    "Bridge-backed frame stepping is not implemented yet. "
                    "Enable allow_stub_steps for API smoke tests."
                )
            frames = action_packet.repeat_frames
            self.budget.record(FrameCategory.TRAINING, frames)
            self._frame_id += frames
            obs = self._placeholder_observation()
            return StepResult(
                observation=obs,
                reward_terms=RewardTerms(),
                info={
                    "stub_step": True,
                    "repeat_frames": frames,
                    "speed_mode": self.speed_mode.value,
                },
            )

    def record_rollout(self, *_args: object, **_kwargs: object) -> None:
        raise NotImplementedError("Rollout recorder is not implemented yet")

    def get_budget_counters(self) -> dict[str, int]:
        return self.budget.snapshot()

    def latest_observation(self) -> ObservationBundle:
        try:
            return self.bridge.latest_observation()
        except NotImplementedError:
            return self._placeholder_observation()

    def get_ram_features(self, probes: list[MemoryProbe] | None = None) -> dict[str, object]:
        try:
            return self.bridge.get_ram_features(probes)
        except NotImplementedError:
            return {
                "traced_state": None,
                "probe_bytes_b64": {},
                "placeholder_ram_export": True,
                "reason": "bridge_not_implemented",
            }

    def _placeholder_observation(self) -> ObservationBundle:
        timing = TimingKeys(
            run_id=self._run_id,
            episode_id=self._episode_id,
            emulator_frame_id=self._frame_id,
            action_frame_id=self._frame_id,
            scenario_id=self.current_scenario.scenario_id if self.current_scenario else None,
        )
        traced = TracedState(frame_id=self._frame_id)
        return ObservationBundle(
            timing=timing,
            traced_state=traced,
            frame_shape=(
                self.config.observation.frame_height,
                self.config.observation.frame_width,
                1 if self.config.observation.grayscale else 3,
            ),
            frame_bytes=None,
            privileged_features={},
            meta_context={
                "speed_mode": self.speed_mode.value,
                "scenario_loaded": self.current_scenario.scenario_id if self.current_scenario else None,
                "match_setup": self.match_setup.notes if self.match_setup else "",
                "stub": True,
            },
        )
