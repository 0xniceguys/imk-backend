"""
Bridge client — JSON-over-Unix-socket protocol to talk to the emulator.

Self-contained: no imports from the training package.
Implements the commands the match runner needs:
  HELLO, LOAD_SAVESTATE, SET_INPUTS, STEP_FRAMES, DEBUGGER_COMMAND
"""

from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BridgeError(RuntimeError):
    pass


class BridgeRemoteError(BridgeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class BridgeResponse:
    payload: dict[str, Any]
    frame_id: int = 0


class EmulatorBridge:
    """
    Minimal bridge client for the match runner.

    Protocol: newline-delimited JSON over a Unix domain socket.
    Request:  {"id": "1", "command": "HELLO", "payload": {}}
    Response: {"id": "1", "ok": true, "payload": {...}, "status": {...}}
    """

    def __init__(self, socket_path: str | Path, *, timeout_sec: float = 120.0) -> None:
        self.socket_path = str(socket_path)
        self.timeout_sec = timeout_sec
        self._socket: socket.socket | None = None
        self._reader = None
        self._writer = None
        self._seq = 0

    def connect(self) -> None:
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_sec)
        sock.connect(self.socket_path)
        self._socket = sock
        self._reader = sock.makefile("r", encoding="utf-8")
        self._writer = sock.makefile("w", encoding="utf-8")

    def close(self) -> None:
        for handle in (self._writer, self._reader):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        self._reader = None
        self._writer = None

    def hello(self) -> BridgeResponse:
        return self._request("HELLO", {})

    def load_savestate(self, path: str) -> BridgeResponse:
        return self._request("LOAD_SAVESTATE", {"savestate_path": path})

    def set_inputs(self, action_packet: dict[str, Any]) -> BridgeResponse:
        """Send controller inputs without advancing frames.

        action_packet should be an encoded ActionPacket dict:
        {
            "macro_action": "ADVANCE" | null,
            "micro_controller_state": {"analog_x": 0.0, "analog_y": 0.0, "pressed": ["D_RIGHT"]},
            "repeat_frames": 1,
            "player": 1,
        }
        """
        return self._request("SET_INPUTS", {"action_packet": action_packet})

    def step_frames(self, action_packet: dict[str, Any]) -> BridgeResponse:
        """Send controller inputs AND advance the emulator by repeat_frames.

        This combines set_inputs + frame advance in one command.
        The emulator applies the inputs then steps forward.
        """
        return self._request("STEP_FRAMES", {"action_packet": action_packet})

    def debugger_command(
        self,
        command: str,
        *,
        timeout_sec: float | None = None,
        output_tail_chars: int = 4000,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"command": command}
        if timeout_sec is not None:
            payload["timeout_sec"] = timeout_sec
        payload["output_tail_chars"] = output_tail_chars
        resp = self._request("DEBUGGER_COMMAND", payload)
        return resp.payload

    def _request(self, command: str, payload: dict[str, Any]) -> BridgeResponse:
        self.connect()
        assert self._writer is not None
        assert self._reader is not None

        self._seq += 1
        req_id = str(self._seq)
        request_obj = {"id": req_id, "command": command, "payload": payload}
        self._writer.write(json.dumps(request_obj) + "\n")
        self._writer.flush()

        line = self._reader.readline()
        if not line:
            raise BridgeError("Bridge server closed connection")

        try:
            resp = json.loads(line)
        except json.JSONDecodeError as e:
            raise BridgeError(f"Invalid JSON from bridge: {e}") from e

        if str(resp.get("id")) != req_id:
            raise BridgeError(f"ID mismatch: expected {req_id}, got {resp.get('id')}")

        if not resp.get("ok", False):
            error = resp.get("error", {})
            raise BridgeRemoteError(
                code=str(error.get("code", "REMOTE_ERROR")),
                message=str(error.get("message", "unknown")),
            )

        status = resp.get("status", {})
        return BridgeResponse(
            payload=resp.get("payload", {}),
            frame_id=int(status.get("frame_id", 0)),
        )


# ── Debugger memory helpers ──

_HEX_RE = re.compile(r"\b([0-9A-Fa-f]{2,16})\b")


def parse_mem_output(output: str) -> list[int]:
    """Parse hex values from a `mem` debugger command response.

    The `mem /1w 0xADDR` output looks like:
        800FE0D8:  00010000
        (dbg)
    The regex matches BOTH the address token and the data token.
    We skip the address prefix by ignoring any token that looks like
    an address (8-char hex on lines that contain ':'), and return
    only the data values.
    """
    values: list[list[int]] = []
    for line in output.replace("\r", "\n").splitlines():
        line = line.strip()
        if not line or line == "(dbg)" or line.startswith("PC at ") or line.startswith("mem "):
            continue
        if line.startswith("write ") or "<-" in line:
            continue
        tokens = _HEX_RE.findall(line)
        if tokens:
            # If line has a colon (address prefix like "800FE0D8:  00010000")
            # skip the first token (the address) and keep the rest (the data)
            if ":" in line and len(tokens) > 1:
                tokens = tokens[1:]
            values.append([int(t, 16) for t in tokens])
    if not values:
        raise ValueError(f"No memory values in debugger output: {output!r}")
    return values[-1]


def read_u8(bridge: EmulatorBridge, virtual_address: int) -> int:
    """Read a single byte from N64 RDRAM via the debugger.

    Applies XOR 0x3 for N64 byte-lane correction.
    """
    dbg_addr = virtual_address ^ 0x3
    resp = bridge.debugger_command(f"mem /1b 0x{dbg_addr:08x}")
    values = parse_mem_output(str(resp.get("output", "")))
    return values[-1] & 0xFF


def read_u32(bridge: EmulatorBridge, virtual_address: int) -> int:
    """Read a 32-bit word from N64 RDRAM via the debugger."""
    resp = bridge.debugger_command(f"mem /1w 0x{virtual_address:08x}")
    values = parse_mem_output(str(resp.get("output", "")))
    return values[-1] & 0xFFFFFFFF

