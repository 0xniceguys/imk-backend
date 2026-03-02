from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper, parse_mem_hex_values  # noqa: E402


class _FakeBridge:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[str] = []

    def debugger_command(
        self,
        command: str,
        *,
        timeout_sec: float | None = None,
        output_tail_chars: int | None = None,
    ) -> dict[str, object]:
        _ = timeout_sec, output_tail_chars
        self.calls.append(command)
        if not self.outputs:
            raise AssertionError("No more fake outputs queued")
        return {
            "command": command,
            "output": self.outputs.pop(0),
            "output_truncated": False,
            "memory_reader": "_FakeBridge",
            "debugger_alive": True,
        }


class Mk4DebugHelperTests(unittest.TestCase):
    def test_parse_mem_hex_values_ignores_debugger_noise(self) -> None:
        output = (
            "\r\nPC at 0x8001FE2C.\r\n"
            "mem /1w 0x800fe758\r\n"
            "00000005 \r\n"
            "(dbg) "
        )
        self.assertEqual(parse_mem_hex_values(output), [0x00000005])

    def test_get_options_difficulty_reads_and_labels_value(self) -> None:
        bridge = _FakeBridge(["mem /1w 0x800fe758\r\n00000004 \r\n(dbg) "])
        helper = Mk4BridgeHelper(bridge=bridge)
        payload = helper.get_options_difficulty()
        self.assertEqual(payload["value"], 4)
        self.assertEqual(payload["label"], "Very Hard")
        self.assertEqual(bridge.calls, ["mem /1w 0x800fe758"])

    def test_get_main_menu_top_level_cursor_reads_and_labels_value(self) -> None:
        bridge = _FakeBridge(["mem /1b 0x8011d813\r\n03 \r\n(dbg) "])
        helper = Mk4BridgeHelper(bridge=bridge)
        payload = helper.get_main_menu_top_level_cursor()
        self.assertEqual(payload["value"], 3)
        self.assertEqual(payload["label"], "Tournament")
        self.assertEqual(bridge.calls, ["mem /1b 0x8011d813"])

    def test_get_arcade_player_count_cursor_reads_and_labels_value(self) -> None:
        bridge = _FakeBridge(["mem /1b 0x8011d813\r\n01 \r\n(dbg) "])
        helper = Mk4BridgeHelper(bridge=bridge)
        payload = helper.get_arcade_player_count_cursor()
        self.assertEqual(payload["value"], 1)
        self.assertEqual(payload["label"], "2 Player")
        self.assertEqual(bridge.calls, ["mem /1b 0x8011d813"])

    def test_get_menu_screen_state_reads_and_labels_value(self) -> None:
        bridge = _FakeBridge(["mem /1b 0x80048d37\r\n0d \r\n(dbg) "])
        helper = Mk4BridgeHelper(bridge=bridge)
        payload = helper.get_menu_screen_state()
        self.assertEqual(payload["value"], 13)
        self.assertEqual(payload["label"], "Options Menu")
        self.assertEqual(bridge.calls, ["mem /1b 0x80048d37"])

    def test_set_options_difficulty_writes_and_verifies(self) -> None:
        bridge = _FakeBridge(
            [
                "write 0x800fe758 w 0x5\r\n0x800fe758 <- 0x00000005\r\n(dbg) ",
                "mem /1w 0x800fe758\r\n00000005 \r\n(dbg) ",
            ]
        )
        helper = Mk4BridgeHelper(bridge=bridge)
        payload = helper.set_options_difficulty("ultimate", verify=True)
        self.assertTrue(payload["match"])
        self.assertEqual(payload["current"]["value"], 5)
        self.assertEqual(
            bridge.calls,
            ["write 0x800fe758 w 0x5", "mem /1w 0x800fe758"],
        )


if __name__ == "__main__":
    unittest.main()
