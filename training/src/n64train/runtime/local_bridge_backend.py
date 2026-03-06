from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Any

from n64train.reverse.mk4_state_contract import (
    MK4_STATE_CONTRACT_VERSION,
    mk4_state_contract_payload,
    mk4_state_payload,
)
from n64train.runtime.actions import ControllerState
from n64train.runtime.events import EventExtractor, SimpleCombatEventExtractor
from n64train.runtime.frame_capture import FrameCapture, ScreenshotPollFrameCapture
from n64train.runtime.launcher import LaunchOptions, Mupen64PlusSession
from n64train.runtime.memory import MemoryProbe, MemoryReader, ZeroMemoryReader
from n64train.runtime.rewards import Mk4ShapedRewardExtractor, RewardExtractor
from n64train.runtime.tracing import NullTraceProvider, TraceProvider
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
class LocalBridgeBackendConfig:
    instance_id: str = "bridge"
    launch_emulator: bool = False
    headed: bool = True
    resolution: str = "320x240"
    speed_mode: SpeedMode = SpeedMode.DEBUG_VISIBLE
    dummy_audio_in_turbo: bool = True
    log_path: Path | None = None


class _DebuggerMemoryReaderBridgeAdapter:
    """Adapts a debugger-capable memory reader to the bridge helper interface."""

    def __init__(self, memory_reader: MemoryReader) -> None:
        self.memory_reader = memory_reader

    def debugger_command(
        self,
        command: str,
        *,
        timeout_sec: float | None = None,
        output_tail_chars: int | None = None,
    ) -> dict[str, Any]:
        handler = getattr(self.memory_reader, "debugger_command", None)
        if not callable(handler):
            raise NotImplementedError("Memory reader does not support debugger commands")

        raw_output = str(handler(command, timeout_s=timeout_sec))
        if output_tail_chars is not None:
            tail_chars = max(256, int(output_tail_chars))
            output = raw_output[-tail_chars:]
        else:
            output = raw_output

        return {
            "command": command,
            "output": output,
        }


class LocalBridgeBackend:
    """
    Real bridge backend with deterministic frame counters and wired subsystems.

    Until Mupen64Plus is patched, tracing/RAM/frame capture are provider-driven:
    - tracing auto-wires the MK4 debugger tracer when the memory reader supports debugger commands
    - otherwise tracing falls back to NullTraceProvider
    - RAM export defaults to ZeroMemoryReader
    - frame capture defaults to screenshot polling
    """

    _episode_seq = count(1)

    def __init__(
        self,
        config: LocalBridgeBackendConfig | None = None,
        *,
        trace_provider: TraceProvider | None = None,
        memory_reader: MemoryReader | None = None,
        frame_capture: FrameCapture | None = None,
        reward_extractor: RewardExtractor | None = None,
        event_extractor: EventExtractor | None = None,
    ) -> None:
        self.config = config or LocalBridgeBackendConfig()
        self.memory_reader = memory_reader or ZeroMemoryReader()
        self.trace_provider = trace_provider or self._build_default_trace_provider(self.memory_reader)
        self.frame_capture = frame_capture or ScreenshotPollFrameCapture(instance_id=self.config.instance_id)
        self.reward_extractor = reward_extractor or Mk4ShapedRewardExtractor()
        self.event_extractor = event_extractor or SimpleCombatEventExtractor()

        self.session: Mupen64PlusSession | None = None
        self.match_setup: MatchSetupSpec | None = None
        self.current_scenario: ScenarioSpec | None = None
        self.pending_reset_savestate_path: str = ""
        self.speed_mode = self.config.speed_mode
        self.episode_id = "episode-0"
        self.frame_id = 0
        self.last_action = ActionPacket(micro_controller_state=ControllerState())
        self.last_traced_state: TracedState | None = None
        self.last_observation: ObservationBundle = self._build_observation(None)

    def _build_default_trace_provider(self, memory_reader: MemoryReader) -> TraceProvider:
        debugger_command = getattr(memory_reader, "debugger_command", None)
        if not callable(debugger_command):
            return NullTraceProvider()

        from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper
        from n64train.reverse.mk4_tracing import Mk4FightTraceProvider

        helper = Mk4BridgeHelper(_DebuggerMemoryReaderBridgeAdapter(memory_reader))
        return Mk4FightTraceProvider(helper=helper)

    def status_payload(self) -> dict[str, Any]:
        return {
            "status": "OK",
            "episode_id": self.episode_id,
            "frame_id": self.frame_id,
            "error_code": None,
        }

    def hello(self) -> dict[str, Any]:
        debugger_command_supported = callable(getattr(self.memory_reader, "debugger_command", None))
        return {
            "backend": "LocalBridgeBackend",
            "instance_id": self.config.instance_id,
            "capabilities": {
                "transport": "unix_socket_json",
                "frame_step": True,
                "set_inputs": True,
                "speed_mode": True,
                "frame_capture": True,
                "frame_capture_mode": "screenshot_poll",
                "ram_export": True,
                "ram_export_mode": self.memory_reader.__class__.__name__,
                "reward_extraction": True,
                "event_extraction": True,
                "deterministic_counter_only": not debugger_command_supported,
                "debugger_command": debugger_command_supported,
                "frame_step_mode": "debugger_cli_frame" if debugger_command_supported else "counter_only",
                "mk4_state_contract": True,
                "mk4_state_contract_version": MK4_STATE_CONTRACT_VERSION,
            },
            "emulator_launched": self.session is not None and self.session.poll() is None,
        }

    def configure_session(self, payload: dict[str, Any]) -> None:
        if "match_setup" in payload:
            self.match_setup = payload["match_setup"]
        if "launch_emulator" in payload:
            # Future hook for dynamic launch control; currently configuration-time only.
            _ = payload["launch_emulator"]
        if self.config.launch_emulator and (self.session is None or self.session.poll() is not None):
            self._start_emulator()

    def set_speed_mode(self, speed_mode: SpeedMode) -> None:
        self.speed_mode = speed_mode
        # Dynamic speed changes on a running Mupen process are not wired yet.
        # The bridge still tracks mode so training code can remain consistent.

    def load_scenario(self, scenario: ScenarioSpec) -> None:
        self.current_scenario = scenario

    def load_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]:
        path = str(savestate_path)
        if not Path(path).exists():
            raise FileNotFoundError(
                f"Savestate file not found: {path!r}. "
                "Check the path stored in the match record."
            )
        output = self._run_debugger_passthrough_command(
            f"stateload {path}",
            ok_token="M64P_STATELOAD_OK",
            timeout_s=45.0,
        )
        self.pending_reset_savestate_path = path
        return {
            "loaded": True,
            "savestate_path": path,
            "debugger_command": "stateload",
            "output_tail": output[-1000:],
        }

    def save_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]:
        path = str(savestate_path)
        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        handler = getattr(self.memory_reader, "debugger_command", None)
        if not callable(handler):
            raise NotImplementedError(
                f"Memory reader {self.memory_reader.__class__.__name__} does not support debugger commands"
            )
        output = str(handler(f"statesave {path}", timeout_s=45.0))
        ok_token_seen = ("M64P_STATESAVE_OK" in output)
        if not ok_token_seen:
            # Some debugger builds occasionally omit the OK marker even when the
            # file is successfully written. Fall back to filesystem verification.
            try:
                file_size = self._wait_for_savestate_file(target_path)
            except Exception as exc:
                tail = output[-2000:]
                raise RuntimeError(
                    f"Debugger command failed: 'statesave {path}'; expected "
                    f"M64P_STATESAVE_OK; tail={tail!r}"
                ) from exc
            return {
                "saved": True,
                "savestate_path": path,
                "debugger_command": "statesave",
                "output_tail": output[-1000:],
                "file_size_bytes": file_size,
                "ok_token_seen": False,
            }
        file_size = self._wait_for_savestate_file(target_path)
        return {
            "saved": True,
            "savestate_path": path,
            "debugger_command": "statesave",
            "output_tail": output[-1000:],
            "file_size_bytes": file_size,
            "ok_token_seen": True,
        }

    def reset_match(self, reset_spec: ResetSpec | None = None) -> ObservationBundle:
        if reset_spec is not None and reset_spec.savestate_path is not None:
            # Keep explicit path as metadata even before emulator savestate RPC is patched.
            self.pending_reset_savestate_path = str(reset_spec.savestate_path)
        elif reset_spec is not None and reset_spec.scenario_id:
            # When only the ID is provided, preserve current loaded scenario metadata.
            self.pending_reset_savestate_path = ""
        self.episode_id = f"episode-{next(self._episode_seq)}"
        self.frame_id = 0
        self.last_traced_state = self.trace_provider.read(self.frame_id)
        self.last_observation = self._build_observation(self.last_traced_state)
        return self.last_observation

    def set_inputs(self, action: ActionPacket) -> None:
        self.last_action = action

    def step(self, action: ActionPacket) -> StepResult:
        self.set_inputs(action)
        prev_state = self.last_traced_state
        frame_step_info: dict[str, Any] = {
            "frame_step_mode": "counter_only",
            "frame_step_ok": True,
            "frame_step_output_tail": "",
        }
        debugger_cmd = getattr(self.memory_reader, "debugger_command", None)
        if callable(debugger_cmd):
            cmd = f"frame {max(1, int(action.repeat_frames))}"
            out = str(debugger_cmd(cmd, timeout_s=max(10.0, float(action.repeat_frames) * 2.0)))
            frame_step_info["frame_step_mode"] = "debugger_cli_frame"
            frame_step_info["frame_step_output_tail"] = out[-1000:]
            ok_match = re.search(r"M64P_FRAME_OK\s+frames=(\d+)", out)
            if ok_match is None:
                frame_step_info["frame_step_ok"] = False
                err_line = "unknown"
                for line in out.splitlines():
                    if "M64P_FRAME_ERR" in line:
                        err_line = line.strip()
                        break
                raise RuntimeError(f"Debugger frame step failed: {err_line}")
            advanced = int(ok_match.group(1))
            self.frame_id += advanced
        else:
            self.frame_id += action.repeat_frames
        next_state = self.trace_provider.read(self.frame_id)
        self.last_traced_state = next_state
        obs = self._build_observation(next_state)
        self.last_observation = obs
        rewards = self.reward_extractor.compute(prev_state, next_state)
        events = self.event_extractor.extract(prev_state, next_state)
        return StepResult(
            observation=obs,
            reward_terms=rewards,
            events=events,
            info={
                "bridge_backend": "local",
                "deterministic_counter_only": frame_step_info["frame_step_mode"] == "counter_only",
                "speed_mode": self.speed_mode.value,
                "repeat_frames": action.repeat_frames,
                "frame_capture_source": obs.meta_context.get("frame_capture_source"),
                **frame_step_info,
            },
        )

    def latest_observation(self) -> ObservationBundle:
        return self.last_observation

    def get_ram_features(self, probes: list[MemoryProbe] | None = None) -> dict[str, Any]:
        probe_data: dict[str, str] = {}
        if probes:
            for probe in probes:
                raw = self.memory_reader.read(probe)
                probe_data[probe.name] = base64.b64encode(raw).decode("ascii")
        traced = self.last_traced_state or self.trace_provider.read(self.frame_id)
        contract_payload = mk4_state_contract_payload()
        state_payload = mk4_state_payload(traced)
        return {
            "traced_state": {
                "frame_id": traced.frame_id,
                "p1_x": traced.p1_x,
                "p2_x": traced.p2_x,
                "p1_y": traced.p1_y,
                "p2_y": traced.p2_y,
                "p1_health": traced.p1_health,
                "p2_health": traced.p2_health,
                "timer": traced.timer,
                "p1_facing": traced.p1_facing,
                "p2_facing": traced.p2_facing,
                "extras": traced.extras,
            },
            "mk4_state_contract": contract_payload,
            "mk4_state_payload": state_payload,
            "probe_bytes_b64": probe_data,
            "memory_reader": self.memory_reader.__class__.__name__,
            "placeholder_ram_export": isinstance(self.memory_reader, ZeroMemoryReader),
        }

    def debugger_command(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        output_tail_chars: int = 12000,
    ) -> dict[str, Any]:
        handler = getattr(self.memory_reader, "debugger_command", None)
        if not callable(handler):
            raise NotImplementedError(
                f"Memory reader {self.memory_reader.__class__.__name__} does not support debugger commands"
            )
        raw_output = str(handler(command, timeout_s=timeout_s))
        tail_chars = max(256, int(output_tail_chars))
        truncated = len(raw_output) > tail_chars
        output = raw_output[-tail_chars:] if truncated else raw_output
        is_alive_fn = getattr(self.memory_reader, "debugger_is_alive", None)
        debugger_alive = bool(is_alive_fn()) if callable(is_alive_fn) else None
        return {
            "command": command,
            "output": output,
            "output_truncated": truncated,
            "memory_reader": self.memory_reader.__class__.__name__,
            "debugger_alive": debugger_alive,
        }

    def terminate(self) -> None:
        if self.session is not None:
            self.session.stop()
        close_fn = getattr(self.memory_reader, "close", None)
        if callable(close_fn):
            close_fn()

    def _run_debugger_passthrough_command(
        self,
        command: str,
        *,
        ok_token: str,
        timeout_s: float,
    ) -> str:
        handler = getattr(self.memory_reader, "debugger_command", None)
        if not callable(handler):
            raise NotImplementedError(
                f"Memory reader {self.memory_reader.__class__.__name__} does not support debugger commands"
            )
        raw_output = str(handler(command, timeout_s=timeout_s))
        if ok_token not in raw_output:
            tail = raw_output[-2000:]
            raise RuntimeError(f"Debugger command failed: {command!r}; expected {ok_token}; tail={tail!r}")
        return raw_output

    def _wait_for_savestate_file(self, path: Path, *, timeout_s: float = 10.0) -> int:
        handler = getattr(self.memory_reader, "debugger_command", None)
        deadline = time.monotonic() + timeout_s
        last_size = -1
        stable_reads = 0

        while time.monotonic() < deadline:
            if path.exists():
                try:
                    size = int(path.stat().st_size)
                except OSError:
                    size = 0
                if size > 0:
                    if size == last_size:
                        stable_reads += 1
                    else:
                        stable_reads = 0
                        last_size = size
                    if stable_reads >= 1:
                        return size
            if callable(handler):
                try:
                    _ = str(handler("frame 2", timeout_s=5.0))
                except Exception:
                    pass
            time.sleep(0.05)
        raise RuntimeError(f"Savestate file was not written within timeout: {path}")

    def _start_emulator(self) -> None:
        options = LaunchOptions(
            speed_mode=self.speed_mode,
            headed=self.config.headed,
            resolution=self.config.resolution,
            instance_id=self.config.instance_id,
            log_path=self.config.log_path,
            dummy_audio_in_turbo=self.config.dummy_audio_in_turbo,
        )
        self.session = Mupen64PlusSession(options)
        self.session.start()

    def _build_observation(self, traced_state: TracedState | None) -> ObservationBundle:
        frame = self.frame_capture.capture()
        timing = TimingKeys(
            run_id=self.config.instance_id,
            episode_id=self.episode_id,
            emulator_frame_id=self.frame_id,
            action_frame_id=self.frame_id,
            scenario_id=self.current_scenario.scenario_id if self.current_scenario else None,
        )
        privileged_features: dict[str, float | int | bool] = {}
        state_payload = mk4_state_payload(traced_state)
        if traced_state is not None:
            if traced_state.p1_x is not None:
                privileged_features["p1_x"] = traced_state.p1_x
            if traced_state.p2_x is not None:
                privileged_features["p2_x"] = traced_state.p2_x
            if traced_state.p1_health is not None:
                privileged_features["p1_health"] = traced_state.p1_health
            if traced_state.p2_health is not None:
                privileged_features["p2_health"] = traced_state.p2_health
            if traced_state.timer is not None:
                privileged_features["timer"] = traced_state.timer
            if traced_state.p1_facing is not None:
                privileged_features["p1_facing"] = traced_state.p1_facing
            if traced_state.p2_facing is not None:
                privileged_features["p2_facing"] = traced_state.p2_facing
            for key in (
                "p1_health_word",
                "p2_health_word",
                "p1_airborne",
                "p2_airborne",
                "p1_y_vel",
                "p1_y_vel_raw",
                "facing_sign",
            ):
                value = state_payload.get(key)
                if isinstance(value, (int, float, bool)):
                    privileged_features[key] = value
        return ObservationBundle(
            timing=timing,
            traced_state=traced_state,
            frame_shape=frame.frame_shape,
            frame_bytes=frame.frame_bytes,
            privileged_features=privileged_features,
            meta_context={
                "speed_mode": self.speed_mode.value,
                "scenario_loaded": self.current_scenario.scenario_id if self.current_scenario else None,
                "frame_capture_source": frame.source,
                "frame_capture_path": frame.path or "",
                "frame_capture_stale": frame.stale,
                "match_setup_notes": self.match_setup.notes if self.match_setup else "",
                "pending_reset_savestate_path": self.pending_reset_savestate_path,
                "placeholder_bridge": True,
            },
        )
