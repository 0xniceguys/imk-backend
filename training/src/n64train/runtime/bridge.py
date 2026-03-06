from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from n64train.runtime.codec import (
    decode_observation_bundle,
    decode_step_result,
    encode_action_packet,
    encode_match_setup_spec,
    encode_memory_probes,
    encode_reset_spec,
    encode_scenario_spec,
    encode_speed_mode,
)
from n64train.runtime.memory import MemoryProbe
from n64train.runtime.types import (
    ActionPacket,
    MatchSetupSpec,
    ObservationBundle,
    ResetSpec,
    ScenarioSpec,
    SpeedMode,
    StepResult,
)


class BridgeCommand(str, Enum):
    HELLO = "HELLO"
    CONFIGURE_SESSION = "CONFIGURE_SESSION"
    LOAD_SAVESTATE = "LOAD_SAVESTATE"
    SAVE_SAVESTATE = "SAVE_SAVESTATE"
    STEP_FRAMES = "STEP_FRAMES"
    SET_INPUTS = "SET_INPUTS"
    GET_OBSERVATION = "GET_OBSERVATION"
    GET_RAM_FEATURES = "GET_RAM_FEATURES"
    DEBUGGER_COMMAND = "DEBUGGER_COMMAND"
    RESET_MATCH = "RESET_MATCH"
    SET_SPEED_MODE = "SET_SPEED_MODE"
    TERMINATE = "TERMINATE"


@dataclass(frozen=True)
class BridgeStatus:
    status: str
    episode_id: str
    frame_id: int
    error_code: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "BridgeStatus":
        payload = payload or {}
        return cls(
            status=str(payload.get("status", "UNKNOWN")),
            episode_id=str(payload.get("episode_id", "")),
            frame_id=int(payload.get("frame_id", 0)),
            error_code=payload.get("error_code"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "episode_id": self.episode_id,
            "frame_id": self.frame_id,
            "error_code": self.error_code,
        }


class BridgeProtocolError(RuntimeError):
    pass


class BridgeRemoteError(RuntimeError):
    def __init__(self, code: str, message: str, status: BridgeStatus | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status = status


@dataclass(frozen=True)
class BridgeResponse:
    payload: dict[str, Any]
    status: BridgeStatus


class SupportsBridgeExtras(Protocol):
    def configure_match(self, setup_spec: MatchSetupSpec) -> None: ...
    def reset_match(self, reset_spec: ResetSpec | None = None) -> ObservationBundle: ...
    def get_ram_features(self, probes: list[MemoryProbe] | None = None) -> dict[str, Any]: ...
    def load_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]: ...
    def save_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]: ...


class EmulatorBridge(Protocol):
    def configure_match(self, setup_spec: MatchSetupSpec) -> None: ...
    def set_speed_mode(self, speed_mode: SpeedMode) -> None: ...
    def load_scenario(self, spec: ScenarioSpec) -> None: ...
    def reset_match(self, reset_spec: ResetSpec | None = None) -> ObservationBundle: ...
    def step(self, action: ActionPacket) -> StepResult: ...
    def latest_observation(self) -> ObservationBundle: ...
    def get_ram_features(self, probes: list[MemoryProbe] | None = None) -> dict[str, Any]: ...
    def load_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]: ...
    def save_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]: ...
    def close(self) -> None: ...


class UnimplementedBridge:
    def configure_match(self, setup_spec: MatchSetupSpec) -> None:
        _ = setup_spec
        raise NotImplementedError("Emulator bridge not implemented yet")

    def set_speed_mode(self, speed_mode: SpeedMode) -> None:
        _ = speed_mode
        raise NotImplementedError("Emulator bridge not implemented yet")

    def load_scenario(self, spec: ScenarioSpec) -> None:
        _ = spec
        raise NotImplementedError("Emulator bridge not implemented yet")

    def reset_match(self, reset_spec: ResetSpec | None = None) -> ObservationBundle:
        _ = reset_spec
        raise NotImplementedError("Emulator bridge not implemented yet")

    def step(self, action: ActionPacket) -> StepResult:
        _ = action
        raise NotImplementedError("Emulator bridge not implemented yet")

    def latest_observation(self) -> ObservationBundle:
        raise NotImplementedError("Emulator bridge not implemented yet")

    def get_ram_features(self, probes: list[MemoryProbe] | None = None) -> dict[str, Any]:
        _ = probes
        raise NotImplementedError("Emulator bridge not implemented yet")

    def load_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]:
        _ = savestate_path
        raise NotImplementedError("Emulator bridge not implemented yet")

    def save_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]:
        _ = savestate_path
        raise NotImplementedError("Emulator bridge not implemented yet")

    def close(self) -> None:
        return


class SocketEmulatorBridge:
    """
    Real local bridge transport over a Unix domain socket.

    The backend may still use placeholder tracing/capture until Mupen is patched,
    but the transport and command surface are real and testable.
    """

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout_sec: float = 5.0,
        terminate_on_close: bool = False,
    ) -> None:
        self.socket_path = str(socket_path)
        self.timeout_sec = timeout_sec
        self.terminate_on_close = terminate_on_close
        self._socket: socket.socket | None = None
        self._reader = None
        self._writer = None
        self._request_seq = 0

    def connect(self) -> None:
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout_sec)
            sock.connect(self.socket_path)
        except Exception:
            sock.close()
            raise
        self._socket = sock
        self._reader = sock.makefile("r", encoding="utf-8")
        self._writer = sock.makefile("w", encoding="utf-8")

    def hello(self) -> BridgeResponse:
        return self._request(BridgeCommand.HELLO, {})

    def configure_match(self, setup_spec: MatchSetupSpec) -> None:
        self._request(
            BridgeCommand.CONFIGURE_SESSION,
            {"match_setup": encode_match_setup_spec(setup_spec)},
        )

    def set_speed_mode(self, speed_mode: SpeedMode) -> None:
        self._request(BridgeCommand.SET_SPEED_MODE, {"speed_mode": encode_speed_mode(speed_mode)})

    def load_scenario(self, spec: ScenarioSpec) -> None:
        self._request(BridgeCommand.LOAD_SAVESTATE, {"scenario_spec": encode_scenario_spec(spec)})

    def load_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]:
        resp = self._request(BridgeCommand.LOAD_SAVESTATE, {"savestate_path": str(savestate_path)})
        return resp.payload

    def save_savestate_path(self, savestate_path: str | Path) -> dict[str, Any]:
        resp = self._request(BridgeCommand.SAVE_SAVESTATE, {"savestate_path": str(savestate_path)})
        return resp.payload

    def reset_match(self, reset_spec: ResetSpec | None = None) -> ObservationBundle:
        resp = self._request(BridgeCommand.RESET_MATCH, {"reset_spec": encode_reset_spec(reset_spec)})
        return decode_observation_bundle(dict(resp.payload["observation"]))

    def set_inputs(self, action: ActionPacket) -> None:
        self._request(BridgeCommand.SET_INPUTS, {"action_packet": encode_action_packet(action)})

    def step(self, action: ActionPacket) -> StepResult:
        resp = self._request(BridgeCommand.STEP_FRAMES, {"action_packet": encode_action_packet(action)})
        return decode_step_result(dict(resp.payload["step_result"]))

    def latest_observation(self) -> ObservationBundle:
        resp = self._request(BridgeCommand.GET_OBSERVATION, {})
        return decode_observation_bundle(dict(resp.payload["observation"]))

    def get_ram_features(self, probes: list[MemoryProbe] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if probes is not None:
            payload["memory_probes"] = encode_memory_probes(probes)
        resp = self._request(BridgeCommand.GET_RAM_FEATURES, payload)
        return resp.payload

    def debugger_command(
        self,
        command: str,
        *,
        timeout_sec: float | None = None,
        output_tail_chars: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"command": str(command)}
        if timeout_sec is not None:
            payload["timeout_sec"] = float(timeout_sec)
        if output_tail_chars is not None:
            payload["output_tail_chars"] = int(output_tail_chars)
        resp = self._request(BridgeCommand.DEBUGGER_COMMAND, payload)
        return resp.payload

    def terminate_server(self) -> None:
        self._request(BridgeCommand.TERMINATE, {})

    def close(self) -> None:
        if self.terminate_on_close:
            try:
                self.terminate_server()
            except Exception:
                pass
        for handle_name in ("_writer", "_reader"):
            handle = getattr(self, handle_name)
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
                setattr(self, handle_name, None)
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def _request(self, command: BridgeCommand, payload: dict[str, Any]) -> BridgeResponse:
        self.connect()
        assert self._writer is not None
        assert self._reader is not None
        self._request_seq += 1
        req_id = str(self._request_seq)
        request_obj = {"id": req_id, "command": command.value, "payload": payload}
        self._writer.write(json.dumps(request_obj) + "\n")
        self._writer.flush()
        line = self._reader.readline()
        if not line:
            raise BridgeProtocolError("Bridge server closed connection")
        try:
            response_obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BridgeProtocolError(f"Invalid bridge JSON response: {exc}") from exc
        if str(response_obj.get("id")) != req_id:
            raise BridgeProtocolError(
                f"Mismatched response id: expected {req_id}, got {response_obj.get('id')}"
            )
        status = BridgeStatus.from_payload(response_obj.get("status"))
        if not bool(response_obj.get("ok", False)):
            error = dict(response_obj.get("error", {}))
            raise BridgeRemoteError(
                code=str(error.get("code", "REMOTE_ERROR")),
                message=str(error.get("message", "unknown bridge error")),
                status=status,
            )
        payload_obj = dict(response_obj.get("payload", {}))
        return BridgeResponse(payload=payload_obj, status=status)
