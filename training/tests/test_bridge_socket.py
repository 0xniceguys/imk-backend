from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.runtime.actions import ControllerState, MacroAction  # noqa: E402
from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402
from n64train.runtime.bridge_server import BridgeServer  # noqa: E402
from n64train.runtime.local_bridge_backend import LocalBridgeBackend, LocalBridgeBackendConfig  # noqa: E402
from n64train.runtime.memory import MemoryProbe, ZeroMemoryReader  # noqa: E402
from n64train.runtime.types import ActionPacket, ResetSpec, SpeedMode  # noqa: E402
from n64train.reverse.mk4_state_contract import MK4_STATE_CONTRACT_VERSION  # noqa: E402


class _FakeTracingMemoryReader(ZeroMemoryReader):
    def __init__(self) -> None:
        self.frames_advanced = 0

    @staticmethod
    def _health_word(health: int) -> int:
        clamped = max(0, min(160, int(health)))
        return int(round((clamped / 160.0) * 0x00010000))

    def debugger_command(self, command: str, *, timeout_s: float | None = None) -> str:
        _ = timeout_s
        if command.startswith("frame "):
            frames = int(command.split()[1])
            self.frames_advanced += frames
            return f"M64P_FRAME_OK frames={frames}"

        if command.startswith("mem /1w "):
            addr = int(command.split()[-1], 16)
            values = {
                0x800FE0D8: self._health_word(max(0, 160 - self.frames_advanced)),
                0x80126F54: self._health_word(max(0, 152 - self.frames_advanced)),
                0x800F87F8: 0x00010000,
                0x8006A060: 0x00050000,
            }
            if addr not in values:
                raise ValueError(f"unexpected word read: 0x{addr:08x}")
            return f"{values[addr]:08X}"

        if command.startswith("mem /1b "):
            addr = int(command.split()[-1], 16)
            values = {
                0x80105118 ^ 0x3: max(0, 99 - self.frames_advanced),
            }
            if addr not in values:
                raise ValueError(f"unexpected byte read: 0x{addr:08x}")
            return f"{values[addr]:02X}"

        raise ValueError(f"unexpected debugger command: {command}")

    def debugger_is_alive(self) -> bool:
        return True


class BridgeSocketTests(unittest.TestCase):
    def test_socket_bridge_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / "mk4.sock"
            backend = LocalBridgeBackend(
                LocalBridgeBackendConfig(
                    instance_id="bridge-test",
                    launch_emulator=False,
                    speed_mode=SpeedMode.DEBUG_VISIBLE,
                )
            )
            server = BridgeServer(sock_path, backend)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self._wait_for_socket(sock_path)
                bridge = SocketEmulatorBridge(sock_path)
                hello = bridge.hello()
                self.assertEqual(hello.payload["backend"], "LocalBridgeBackend")
                self.assertTrue(hello.payload["capabilities"]["mk4_state_contract"])
                self.assertEqual(
                    hello.payload["capabilities"]["mk4_state_contract_version"],
                    MK4_STATE_CONTRACT_VERSION,
                )

                reset_obs = bridge.reset_match(ResetSpec())
                self.assertEqual(reset_obs.timing.emulator_frame_id, 0)

                result = bridge.step(
                    ActionPacket(
                        macro_action=MacroAction.ADVANCE,
                        micro_controller_state=ControllerState(),
                        repeat_frames=4,
                    )
                )
                self.assertEqual(result.observation.timing.emulator_frame_id, 4)
                self.assertEqual(result.info["repeat_frames"], 4)

                latest = bridge.latest_observation()
                self.assertEqual(latest.timing.emulator_frame_id, 4)

                ram = bridge.get_ram_features([MemoryProbe(name="test", address=0x10, size=4)])
                self.assertIn("probe_bytes_b64", ram)
                self.assertIn("test", ram["probe_bytes_b64"])

                bridge.terminate_server()
                bridge.close()
            finally:
                server.shutdown()
                thread.join(timeout=2.0)

    def test_socket_bridge_populates_traced_health_from_debugger_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / "mk4.sock"
            backend = LocalBridgeBackend(
                LocalBridgeBackendConfig(
                    instance_id="bridge-tracing-test",
                    launch_emulator=False,
                    speed_mode=SpeedMode.DEBUG_VISIBLE,
                ),
                memory_reader=_FakeTracingMemoryReader(),
            )
            server = BridgeServer(sock_path, backend)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            bridge = None
            try:
                self._wait_for_socket(sock_path)
                bridge = SocketEmulatorBridge(sock_path)

                reset_obs = bridge.reset_match(ResetSpec())
                self.assertEqual(reset_obs.traced_state.p1_health, 160)
                self.assertEqual(reset_obs.traced_state.p2_health, 152)
                self.assertEqual(reset_obs.privileged_features["p1_health"], 160)
                self.assertEqual(reset_obs.privileged_features["p2_health"], 152)
                self.assertEqual(reset_obs.meta_context["mk4_state_contract_version"], MK4_STATE_CONTRACT_VERSION)
                self.assertEqual(reset_obs.meta_context["mk4_state_payload"]["p1_health_word"], 0x00010000)

                result = bridge.step(
                    ActionPacket(
                        macro_action=MacroAction.ADVANCE,
                        micro_controller_state=ControllerState(),
                        repeat_frames=4,
                    )
                )
                self.assertEqual(result.observation.traced_state.p1_health, 156)
                self.assertEqual(result.observation.traced_state.p2_health, 148)
                self.assertEqual(result.observation.privileged_features["p1_health"], 156)
                self.assertEqual(result.observation.privileged_features["p2_health"], 148)
                self.assertEqual(
                    result.observation.privileged_features["p1_health_word"],
                    _FakeTracingMemoryReader._health_word(156),
                )
                self.assertEqual(
                    result.observation.privileged_features["p2_health_word"],
                    _FakeTracingMemoryReader._health_word(148),
                )

                ram = bridge.get_ram_features()
                self.assertEqual(ram["mk4_state_contract"]["version"], MK4_STATE_CONTRACT_VERSION)
                self.assertEqual(ram["mk4_state_payload"]["p1_health"], 156)
                self.assertEqual(ram["mk4_state_payload"]["p2_health"], 148)
            finally:
                try:
                    if bridge is not None:
                        bridge.terminate_server()
                        bridge.close()
                except Exception:
                    pass
                server.shutdown()
                thread.join(timeout=2.0)

    def _wait_for_socket(self, sock_path: Path) -> None:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if sock_path.exists():
                return
            time.sleep(0.02)
        self.fail(f"Socket did not appear: {sock_path}")


if __name__ == "__main__":
    unittest.main()
