from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from n64train.reverse.mk4_findings import (
    MK4_ARCADE_PLAYER_COUNT_CURSOR,
    MK4_MENU_SCREEN_STATE,
    MK4_OPTIONS_DIFFICULTY,
    MK4_TOP_LEVEL_MENU_CURSOR,
    Mk4OptionsDifficulty,
    arcade_player_count_cursor_label,
    menu_screen_state_label,
    normalize_options_difficulty,
    options_difficulty_label,
    top_level_menu_cursor_label,
)


class DebuggerBridgeLike(Protocol):
    def debugger_command(
        self,
        command: str,
        *,
        timeout_sec: float | None = None,
        output_tail_chars: int | None = None,
    ) -> dict[str, object]: ...


_HEX_TOKEN_RE = re.compile(r"\b([0-9A-Fa-f]{2,16})\b")


def _normalize_output(output: str) -> list[str]:
    return [line.strip() for line in output.replace("\r", "\n").splitlines() if line.strip()]


def parse_mem_hex_values(output: str) -> list[int]:
    """
    Parse debugger `mem` command output and return the final displayed row of hex values.

    The debugger may emit noise lines such as `PC at ...` before echoing the command.
    """

    lines = _normalize_output(output)
    value_lines: list[list[int]] = []
    for line in lines:
        if line == "(dbg)":
            continue
        if line.startswith("PC at "):
            continue
        if line.startswith("mem "):
            continue
        if line.startswith("write "):
            continue
        if "<-" in line:
            continue
        tokens = _HEX_TOKEN_RE.findall(line)
        if not tokens:
            continue
        value_lines.append([int(tok, 16) for tok in tokens])
    if not value_lines:
        raise ValueError(f"No memory values found in debugger output: {output!r}")
    return value_lines[-1]


@dataclass
class Mk4BridgeHelper:
    bridge: DebuggerBridgeLike
    debugger_timeout_sec: float = 5.0
    output_tail_chars: int = 4000

    def pause(self) -> dict[str, object]:
        return self._dbg("pause")

    def run(self) -> dict[str, object]:
        return self._dbg("run")

    def read_u8(self, virtual_address: int) -> int:
        # `mem /b` in the debugger reflects N64 byte-lane ordering, while our symbols
        # are tracked against raw RDRAM dump offsets. Translate to the debugger-visible
        # byte lane so helper reads match snapshot-derived addresses.
        debugger_byte_address = virtual_address ^ 0x3
        resp = self._dbg(f"mem /1b 0x{debugger_byte_address:08x}")
        values = parse_mem_hex_values(str(resp.get("output", "")))
        if len(values) != 1:
            raise ValueError(f"Expected one byte, got {len(values)} from debugger output")
        return int(values[0]) & 0xFF

    def read_u32(self, virtual_address: int) -> int:
        resp = self._dbg(f"mem /1w 0x{virtual_address:08x}")
        values = parse_mem_hex_values(str(resp.get("output", "")))
        if len(values) != 1:
            raise ValueError(f"Expected one 32-bit word, got {len(values)} from debugger output")
        return int(values[0]) & 0xFFFFFFFF

    def write_u32(self, virtual_address: int, value: int) -> dict[str, object]:
        return self._dbg(f"write 0x{virtual_address:08x} w 0x{value & 0xFFFFFFFF:x}")

    def get_options_difficulty(self) -> dict[str, object]:
        value = self.read_u32(MK4_OPTIONS_DIFFICULTY.virtual_address)
        return {
            "symbol": MK4_OPTIONS_DIFFICULTY.to_dict(),
            "value": value,
            "value_hex": f"0x{value:08X}",
            "label": options_difficulty_label(value),
        }

    def get_main_menu_top_level_cursor(self) -> dict[str, object]:
        value = self.read_u8(MK4_TOP_LEVEL_MENU_CURSOR.virtual_address)
        return {
            "symbol": MK4_TOP_LEVEL_MENU_CURSOR.to_dict(),
            "value": value,
            "value_hex": f"0x{value:02X}",
            "label": top_level_menu_cursor_label(value),
            "note": "Meaningful on the top-level main menu screen.",
        }

    def get_arcade_player_count_cursor(self) -> dict[str, object]:
        value = self.read_u8(MK4_ARCADE_PLAYER_COUNT_CURSOR.virtual_address)
        return {
            "symbol": MK4_ARCADE_PLAYER_COUNT_CURSOR.to_dict(),
            "value": value,
            "value_hex": f"0x{value:02X}",
            "label": arcade_player_count_cursor_label(value),
            "note": "Meaningful on the Arcade 1P/2P screen (screen-state primary=0 with non-intro secondary signature).",
        }

    def get_menu_screen_state(self) -> dict[str, object]:
        value = self.read_u8(MK4_MENU_SCREEN_STATE.virtual_address)
        return {
            "symbol": MK4_MENU_SCREEN_STATE.to_dict(),
            "value": value,
            "value_hex": f"0x{value:02X}",
            "label": menu_screen_state_label(value),
            "note": (
                "Observed values currently mapped: 0=Intro/Attract, 6=Top-Level Main Menu, "
                "10=Arcade Rumble Warning, 13=Options Menu. More screens will be added as they are discovered."
            ),
        }

    def set_options_difficulty(
        self,
        target: str | int | Mk4OptionsDifficulty,
        *,
        verify: bool = True,
    ) -> dict[str, object]:
        target_enum = normalize_options_difficulty(target)
        write_resp = self.write_u32(MK4_OPTIONS_DIFFICULTY.virtual_address, int(target_enum))
        result = {
            "symbol": MK4_OPTIONS_DIFFICULTY.to_dict(),
            "requested": {
                "value": int(target_enum),
                "label": options_difficulty_label(int(target_enum)),
            },
            "write_response": write_resp,
        }
        if verify:
            current = self.get_options_difficulty()
            result["verified"] = True
            result["current"] = current
            result["match"] = current["value"] == int(target_enum)
        return result

    def _dbg(self, command: str) -> dict[str, object]:
        return self.bridge.debugger_command(
            command,
            timeout_sec=self.debugger_timeout_sec,
            output_tail_chars=self.output_tail_chars,
        )
