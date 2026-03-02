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
from n64train.runtime.memory import MemoryProbe  # noqa: E402
from n64train.runtime.types import ActionPacket, ResetSpec, SpeedMode  # noqa: E402


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

    def _wait_for_socket(self, sock_path: Path) -> None:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if sock_path.exists():
                return
            time.sleep(0.02)
        self.fail(f"Socket did not appear: {sock_path}")


if __name__ == "__main__":
    unittest.main()
