from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from n64train.reverse.mk4_findings import (  # noqa: E402
    MK4_ARCADE_PLAYER_COUNT_CURSOR,
    MK4_MENU_SCREEN_STATE,
    MK4_OPTIONS_DIFFICULTY,
    MK4_TOP_LEVEL_MENU_CURSOR,
    Mk4ArcadePlayerCountCursor,
    Mk4MenuScreenState,
    Mk4OptionsDifficulty,
    Mk4TopLevelMenuCursor,
    arcade_player_count_cursor_label,
    menu_screen_state_label,
    mk4_symbol_registry,
    normalize_options_difficulty,
    options_difficulty_label,
    top_level_menu_cursor_label,
)


class Mk4FindingsTests(unittest.TestCase):
    def test_options_difficulty_symbol_is_registered(self) -> None:
        registry = mk4_symbol_registry()
        self.assertIn("options_difficulty", registry)
        self.assertEqual(registry["options_difficulty"].virtual_address, 0x800FE758)
        self.assertEqual(registry["options_difficulty"].width_bytes, 4)

    def test_main_menu_cursor_symbol_is_registered(self) -> None:
        registry = mk4_symbol_registry()
        self.assertIn("main_menu_top_level_cursor", registry)
        self.assertEqual(registry["main_menu_top_level_cursor"].virtual_address, 0x8011D810)
        self.assertEqual(registry["main_menu_top_level_cursor"].width_bytes, 1)

    def test_arcade_player_count_cursor_symbol_is_registered(self) -> None:
        registry = mk4_symbol_registry()
        self.assertIn("arcade_player_count_cursor", registry)
        self.assertEqual(registry["arcade_player_count_cursor"].virtual_address, 0x8011D810)
        self.assertEqual(registry["arcade_player_count_cursor"].width_bytes, 1)

    def test_menu_screen_state_symbol_is_registered(self) -> None:
        registry = mk4_symbol_registry()
        self.assertIn("menu_screen_state", registry)
        self.assertEqual(registry["menu_screen_state"].virtual_address, 0x80048D34)
        self.assertEqual(registry["menu_screen_state"].width_bytes, 1)

    def test_normalize_options_difficulty_accepts_labels_and_values(self) -> None:
        self.assertEqual(normalize_options_difficulty("ultimate"), Mk4OptionsDifficulty.ULTIMATE)
        self.assertEqual(normalize_options_difficulty("very_hard"), Mk4OptionsDifficulty.VERY_HARD)
        self.assertEqual(normalize_options_difficulty("5"), Mk4OptionsDifficulty.ULTIMATE)
        self.assertEqual(normalize_options_difficulty(2), Mk4OptionsDifficulty.MEDIUM)

    def test_normalize_options_difficulty_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            normalize_options_difficulty("nightmare")

    def test_options_difficulty_label_formats_known_and_unknown(self) -> None:
        self.assertEqual(options_difficulty_label(4), "Very Hard")
        self.assertEqual(options_difficulty_label(Mk4OptionsDifficulty.ULTIMATE), "Ultimate")
        self.assertEqual(options_difficulty_label(99), "Unknown(99)")

    def test_top_level_menu_cursor_label_formats_known_and_unknown(self) -> None:
        self.assertEqual(top_level_menu_cursor_label(3), "Tournament")
        self.assertEqual(top_level_menu_cursor_label(Mk4TopLevelMenuCursor.OPTIONS), "Options")
        self.assertEqual(top_level_menu_cursor_label(99), "Unknown(99)")

    def test_arcade_player_count_cursor_label_formats_known_and_unknown(self) -> None:
        self.assertEqual(arcade_player_count_cursor_label(0), "1 Player")
        self.assertEqual(
            arcade_player_count_cursor_label(Mk4ArcadePlayerCountCursor.TWO_PLAYER),
            "2 Player",
        )
        self.assertEqual(arcade_player_count_cursor_label(99), "Unknown(99)")

    def test_menu_screen_state_label_formats_known_and_unknown(self) -> None:
        self.assertEqual(menu_screen_state_label(0), "Intro / Attract Screen")
        self.assertEqual(menu_screen_state_label(6), "Top-Level Main Menu")
        self.assertEqual(menu_screen_state_label(10), "Arcade Rumble Warning")
        self.assertEqual(menu_screen_state_label(Mk4MenuScreenState.OPTIONS_MENU), "Options Menu")
        self.assertEqual(menu_screen_state_label(99), "Unknown(99)")

    def test_symbol_dict_includes_enum_mapping(self) -> None:
        payload = MK4_OPTIONS_DIFFICULTY.to_dict()
        self.assertEqual(payload["virtual_address_hex"], "0x800FE758")
        self.assertEqual(payload["enum_labels"]["5"], "Ultimate")

    def test_main_menu_cursor_symbol_dict_includes_enum_mapping(self) -> None:
        payload = MK4_TOP_LEVEL_MENU_CURSOR.to_dict()
        self.assertEqual(payload["virtual_address_hex"], "0x8011D810")
        self.assertEqual(payload["enum_labels"]["3"], "Tournament")

    def test_arcade_player_count_cursor_symbol_dict_includes_enum_mapping(self) -> None:
        payload = MK4_ARCADE_PLAYER_COUNT_CURSOR.to_dict()
        self.assertEqual(payload["virtual_address_hex"], "0x8011D810")
        self.assertEqual(payload["enum_labels"]["0"], "1 Player")
        self.assertEqual(payload["enum_labels"]["1"], "2 Player")

    def test_menu_screen_state_symbol_dict_includes_enum_mapping(self) -> None:
        payload = MK4_MENU_SCREEN_STATE.to_dict()
        self.assertEqual(payload["virtual_address_hex"], "0x80048D34")
        self.assertEqual(payload["enum_labels"]["0"], "Intro / Attract Screen")
        self.assertEqual(payload["enum_labels"]["6"], "Top-Level Main Menu")
        self.assertEqual(payload["enum_labels"]["10"], "Arcade Rumble Warning")


if __name__ == "__main__":
    unittest.main()
