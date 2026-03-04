from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any

from n64train.runtime.bridge import BridgeCommand
from n64train.runtime.codec import (
    decode_action_packet,
    decode_match_setup_spec,
    decode_memory_probes,
    decode_reset_spec,
    decode_scenario_spec,
    decode_speed_mode,
    encode_observation_bundle,
    encode_step_result,
)
from n64train.runtime.local_bridge_backend import LocalBridgeBackend


class BridgeServer:
    def __init__(self, socket_path: str | Path, backend: LocalBridgeBackend) -> None:
        self.socket_path = str(socket_path)
        self.backend = backend
        self._stop_event = threading.Event()
        self._server_socket: socket.socket | None = None

    def serve_forever(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket = sock
        path = Path(self.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        sock.bind(self.socket_path)
        sock.listen(4)
        sock.settimeout(0.2)
        try:
            while not self._stop_event.is_set():
                try:
                    conn, _ = sock.accept()
                except socket.timeout:
                    continue
                with conn:
                    conn.settimeout(120.0)
                    reader = conn.makefile("r", encoding="utf-8")
                    writer = conn.makefile("w", encoding="utf-8")
                    try:
                        for line in reader:
                            if not line:
                                break
                            response = self._handle_line(line)
                            try:
                                writer.write(json.dumps(response) + "\n")
                                writer.flush()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                break
                            if self._stop_event.is_set():
                                break
                    except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
                        # Per-connection I/O failure should not kill the bridge server.
                        pass
                    except Exception:
                        # Catch-all: any unhandled error (e.g. TypeError from
                        # json.dumps of non-serializable response) must NOT kill
                        # the server or delete the socket — just drop this connection.
                        pass
                    finally:
                        try:
                            writer.close()
                        except Exception:
                            pass
                        try:
                            reader.close()
                        except Exception:
                            pass
        finally:
            self.backend.terminate()
            try:
                sock.close()
            finally:
                self._server_socket = None
            if os.path.exists(self.socket_path):
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass

    def shutdown(self) -> None:
        self._stop_event.set()

    def _handle_line(self, line: str) -> dict[str, Any]:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return self._error_response("?", "BAD_JSON", str(exc))

        req_id = str(request.get("id", "?"))
        cmd_raw = request.get("command")
        payload = dict(request.get("payload", {}))

        try:
            command = BridgeCommand(str(cmd_raw))
        except Exception:
            return self._error_response(req_id, "UNKNOWN_COMMAND", f"Unknown command: {cmd_raw}")

        try:
            response_payload = self._dispatch(command, payload)
            return {
                "id": req_id,
                "ok": True,
                "status": self.backend.status_payload(),
                "payload": response_payload,
            }
        except NotImplementedError as exc:
            return self._error_response(req_id, "NOT_IMPLEMENTED", str(exc))
        except Exception as exc:
            return self._error_response(req_id, "BACKEND_ERROR", str(exc))

    def _dispatch(self, command: BridgeCommand, payload: dict[str, Any]) -> dict[str, Any]:
        if command is BridgeCommand.HELLO:
            return self.backend.hello()

        if command is BridgeCommand.CONFIGURE_SESSION:
            if "match_setup" in payload:
                self.backend.configure_session({"match_setup": decode_match_setup_spec(dict(payload["match_setup"]))})
            else:
                self.backend.configure_session(payload)
            return {"configured": True}

        if command is BridgeCommand.LOAD_SAVESTATE:
            if "scenario_spec" in payload:
                self.backend.load_scenario(decode_scenario_spec(dict(payload["scenario_spec"])))
            elif "savestate_path" in payload:
                return self.backend.load_savestate_path(str(payload["savestate_path"]))
            else:
                raise NotImplementedError("LOAD_SAVESTATE currently expects scenario_spec")
            return {"loaded": True}

        if command is BridgeCommand.SAVE_SAVESTATE:
            savestate_path = str(payload.get("savestate_path", ""))
            if not savestate_path:
                raise ValueError("SAVE_SAVESTATE requires 'savestate_path'")
            return self.backend.save_savestate_path(savestate_path)

        if command is BridgeCommand.SET_INPUTS:
            self.backend.set_inputs(decode_action_packet(dict(payload["action_packet"])))
            return {"inputs_set": True}

        if command is BridgeCommand.STEP_FRAMES:
            step_result = self.backend.step(decode_action_packet(dict(payload["action_packet"])))
            return {"step_result": encode_step_result(step_result)}

        if command is BridgeCommand.GET_OBSERVATION:
            obs = self.backend.latest_observation()
            return {"observation": encode_observation_bundle(obs)}

        if command is BridgeCommand.GET_RAM_FEATURES:
            probes = None
            if "memory_probes" in payload:
                probes = decode_memory_probes(list(payload["memory_probes"]))
            return self.backend.get_ram_features(probes)

        if command is BridgeCommand.DEBUGGER_COMMAND:
            command_text = str(payload.get("command", ""))
            if not command_text.strip():
                raise ValueError("DEBUGGER_COMMAND requires non-empty 'command'")
            timeout_sec = payload.get("timeout_sec")
            output_tail_chars = payload.get("output_tail_chars", 12000)
            return self.backend.debugger_command(
                command_text,
                timeout_s=float(timeout_sec) if timeout_sec is not None else None,
                output_tail_chars=int(output_tail_chars),
            )

        if command is BridgeCommand.RESET_MATCH:
            reset_spec_payload = payload.get("reset_spec", {})
            obs = self.backend.reset_match(decode_reset_spec(dict(reset_spec_payload)))
            return {"observation": encode_observation_bundle(obs)}

        if command is BridgeCommand.SET_SPEED_MODE:
            self.backend.set_speed_mode(decode_speed_mode(str(payload["speed_mode"])))
            return {"speed_mode": str(payload["speed_mode"])}

        if command is BridgeCommand.TERMINATE:
            self.shutdown()
            return {"terminating": True}

        raise NotImplementedError(f"Unhandled command: {command.value}")

    def _error_response(self, req_id: str, code: str, message: str) -> dict[str, Any]:
        status = self.backend.status_payload()
        status["status"] = "ERROR"
        status["error_code"] = code
        return {
            "id": req_id,
            "ok": False,
            "status": status,
            "error": {"code": code, "message": message},
            "payload": {},
        }
