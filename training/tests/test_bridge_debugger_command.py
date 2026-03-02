from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.runtime.bridge import SocketEmulatorBridge  # noqa: E402
from n64train.runtime.bridge_server import BridgeServer  # noqa: E402
from n64train.runtime.local_bridge_backend import LocalBridgeBackend, LocalBridgeBackendConfig  # noqa: E402
from n64train.runtime.memory import ZeroMemoryReader  # noqa: E402


class _FakeDebuggerMemoryReader(ZeroMemoryReader):
    def __init__(self) -> None:
        self.calls: list[tuple[str, float | None]] = []

    def debugger_command(self, command: str, *, timeout_s: float | None = None) -> str:
        self.calls.append((command, timeout_s))
        return f"fake-debugger: {command}"

    def debugger_is_alive(self) -> bool:
        return True


class BridgeDebuggerCommandTests(unittest.TestCase):
    def test_debugger_command_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / "mk4.sock"
            fake_reader = _FakeDebuggerMemoryReader()
            backend = LocalBridgeBackend(
                LocalBridgeBackendConfig(instance_id="bridge-debugger-test", launch_emulator=False),
                memory_reader=fake_reader,
            )
            server = BridgeServer(sock_path, backend)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            bridge = None
            try:
                self._wait_for_socket(sock_path)
                bridge = SocketEmulatorBridge(sock_path)
                hello = bridge.hello()
                self.assertTrue(hello.payload["capabilities"]["debugger_command"])

                result = bridge.debugger_command("pause", timeout_sec=3.0, output_tail_chars=1000)
                self.assertEqual(result["command"], "pause")
                self.assertEqual(result["output"], "fake-debugger: pause")
                self.assertFalse(result["output_truncated"])
                self.assertEqual(result["memory_reader"], "_FakeDebuggerMemoryReader")
                self.assertTrue(result["debugger_alive"])

                self.assertEqual(fake_reader.calls, [("pause", 3.0)])
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
