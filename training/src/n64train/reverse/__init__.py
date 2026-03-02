"""Reverse-engineering tooling for MK4 memory/state discovery via the bridge."""

from n64train.reverse.diff import ByteDiffRun, DiffSummary, diff_bytes
from n64train.reverse.mk4_debug_helpers import Mk4BridgeHelper, parse_mem_hex_values
from n64train.reverse.mk4_findings import (
    MK4_OPTIONS_DIFFICULTY,
    MK4_TOP_LEVEL_MENU_CURSOR,
    Mk4MemorySymbol,
    Mk4OptionsDifficulty,
    Mk4TopLevelMenuCursor,
    mk4_symbol_registry,
    normalize_options_difficulty,
    options_difficulty_label,
    top_level_menu_cursor_label,
)
from n64train.reverse.scanner import (
    AddressRange,
    BridgeMemoryScanner,
    MemoryRangeSnapshot,
    chunked_memory_probes,
)
from n64train.reverse.tasks import ReverseTask, default_reverse_tasks

__all__ = [
    "AddressRange",
    "BridgeMemoryScanner",
    "MemoryRangeSnapshot",
    "ByteDiffRun",
    "DiffSummary",
    "MK4_OPTIONS_DIFFICULTY",
    "MK4_TOP_LEVEL_MENU_CURSOR",
    "Mk4BridgeHelper",
    "Mk4MemorySymbol",
    "Mk4OptionsDifficulty",
    "Mk4TopLevelMenuCursor",
    "ReverseTask",
    "chunked_memory_probes",
    "default_reverse_tasks",
    "diff_bytes",
    "mk4_symbol_registry",
    "normalize_options_difficulty",
    "options_difficulty_label",
    "top_level_menu_cursor_label",
    "parse_mem_hex_values",
]
