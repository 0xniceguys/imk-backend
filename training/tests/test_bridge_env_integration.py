from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.envs.mk4_lowlevel import MK4EnvConfig, MK4LowLevelEnv  # noqa: E402
from n64train.runtime.actions import ControllerState, MacroAction  # noqa: E402
from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402
from n64train.runtime.bridge_server import BridgeServer  # noqa: E402
from n64train.runtime.local_bridge_backend import LocalBridgeBackend, LocalBridgeBackendConfig  # noqa: E402
from n64train.runtime.types import ActionPacket, SpeedMode  # noqa: E402


class BridgeEnvIntegrationTests(unittest.TestCase):
    def test_env_step_uses_bridge_and_updates_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / "mk4.sock"
            backend = LocalBridgeBackend(LocalBridgeBackendConfig(instance_id="env-bridge", launch_emulator=False))
            server = BridgeServer(sock_path, backend)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self._wait_for_socket(sock_path)
                bridge = SocketEmulatorBridge(sock_path)
                env = MK4LowLevelEnv(
                    MK4EnvConfig(
                        allow_stub_steps=False,
                        speed_mode=SpeedMode.TRAIN_TURBO,
                        instance_id="env-client",
                        max_env_frames=20,
                    ),
                    bridge=bridge,
                )
                obs = env.reset()
                self.assertEqual(obs.timing.emulator_frame_id, 0)

                result = env.step(
                    ActionPacket(
                        macro_action=MacroAction.PUNISH,
                        micro_controller_state=ControllerState(),
                        repeat_frames=2,
                    )
                )
                self.assertEqual(result.observation.timing.emulator_frame_id, 2)
                self.assertEqual(env.get_budget_counters()["training"], 2)
                self.assertFalse(result.info.get("stub_step", False))
            finally:
                try:
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
