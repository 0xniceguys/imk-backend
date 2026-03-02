from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping


class Mk4OptionsDifficulty(IntEnum):
    VERY_EASY = 0
    EASY = 1
    MEDIUM = 2
    HARD = 3
    VERY_HARD = 4
    ULTIMATE = 5


class Mk4TopLevelMenuCursor(IntEnum):
    ARCADE = 0
    TEAM = 1
    ENDURANCE = 2
    TOURNAMENT = 3
    PRACTICE = 4
    OPTIONS = 5


class Mk4ArcadePlayerCountCursor(IntEnum):
    ONE_PLAYER = 0
    TWO_PLAYER = 1


class Mk4MenuScreenState(IntEnum):
    INTRO_ATTRACT = 0
    TOP_LEVEL_MAIN_MENU = 6
    ARCADE_RUMBLE_WARNING = 10
    OPTIONS_MENU = 13
    CHARACTER_SELECT = 38


_OPTIONS_DIFFICULTY_LABELS: dict[int, str] = {
    Mk4OptionsDifficulty.VERY_EASY: "Very Easy",
    Mk4OptionsDifficulty.EASY: "Easy",
    Mk4OptionsDifficulty.MEDIUM: "Medium",
    Mk4OptionsDifficulty.HARD: "Hard",
    Mk4OptionsDifficulty.VERY_HARD: "Very Hard",
    Mk4OptionsDifficulty.ULTIMATE: "Ultimate",
}

_TOP_LEVEL_MENU_CURSOR_LABELS: dict[int, str] = {
    Mk4TopLevelMenuCursor.ARCADE: "Arcade",
    Mk4TopLevelMenuCursor.TEAM: "Team",
    Mk4TopLevelMenuCursor.ENDURANCE: "Endurance",
    Mk4TopLevelMenuCursor.TOURNAMENT: "Tournament",
    Mk4TopLevelMenuCursor.PRACTICE: "Practice",
    Mk4TopLevelMenuCursor.OPTIONS: "Options",
}

_ARCADE_PLAYER_COUNT_CURSOR_LABELS: dict[int, str] = {
    Mk4ArcadePlayerCountCursor.ONE_PLAYER: "1 Player",
    Mk4ArcadePlayerCountCursor.TWO_PLAYER: "2 Player",
}

_MENU_SCREEN_STATE_LABELS: dict[int, str] = {
    Mk4MenuScreenState.INTRO_ATTRACT: "Intro / Attract Screen",
    Mk4MenuScreenState.TOP_LEVEL_MAIN_MENU: "Top-Level Main Menu",
    Mk4MenuScreenState.ARCADE_RUMBLE_WARNING: "Arcade Rumble Warning",
    Mk4MenuScreenState.OPTIONS_MENU: "Options Menu",
    Mk4MenuScreenState.CHARACTER_SELECT: "Character Select",
}


_OPTIONS_DIFFICULTY_ALIASES: dict[str, Mk4OptionsDifficulty] = {
    "very easy": Mk4OptionsDifficulty.VERY_EASY,
    "veryeasy": Mk4OptionsDifficulty.VERY_EASY,
    "easy": Mk4OptionsDifficulty.EASY,
    "medium": Mk4OptionsDifficulty.MEDIUM,
    "normal": Mk4OptionsDifficulty.MEDIUM,
    "hard": Mk4OptionsDifficulty.HARD,
    "very hard": Mk4OptionsDifficulty.VERY_HARD,
    "veryhard": Mk4OptionsDifficulty.VERY_HARD,
    "vhard": Mk4OptionsDifficulty.VERY_HARD,
    "vh": Mk4OptionsDifficulty.VERY_HARD,
    "ultimate": Mk4OptionsDifficulty.ULTIMATE,
    "max": Mk4OptionsDifficulty.ULTIMATE,
}


@dataclass(frozen=True)
class Mk4MemorySymbol:
    key: str
    label: str
    virtual_address: int
    width_bytes: int
    kind: str
    description: str
    confidence: float = 1.0
    notes: str = ""
    enum_labels: Mapping[int, str] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "key": self.key,
            "label": self.label,
            "virtual_address": self.virtual_address,
            "virtual_address_hex": f"0x{self.virtual_address:08X}",
            "width_bytes": self.width_bytes,
            "kind": self.kind,
            "description": self.description,
            "confidence": self.confidence,
            "notes": self.notes,
        }
        if self.enum_labels is not None:
            payload["enum_labels"] = {
                str(int(k)): v for k, v in sorted(self.enum_labels.items(), key=lambda item: int(item[0]))
            }
        return payload


MK4_OPTIONS_DIFFICULTY = Mk4MemorySymbol(
    key="options_difficulty",
    label="Options -> Difficulty",
    virtual_address=0x800FE758,
    width_bytes=4,
    kind="u32_enum",
    description="Global CPU difficulty in Options menu",
    confidence=0.99,
    notes=(
        "Discovered via 4-step RAM diff on Options->Difficulty and confirmed by direct "
        "write/read-back while paused on the options screen."
    ),
    enum_labels=_OPTIONS_DIFFICULTY_LABELS,
)

MK4_TOP_LEVEL_MENU_CURSOR = Mk4MemorySymbol(
    key="main_menu_top_level_cursor",
    label="Main Menu -> Top-Level Cursor Index",
    virtual_address=0x8011D810,
    width_bytes=1,
    kind="u8_enum",
    description="Top-level main menu highlighted row index (Arcade..Options) when on the top-level menu screen",
    confidence=0.95,
    notes=(
        "Discovered via controlled same-screen cursor moves on the top-level menu. "
        "Observed sequence 0,1,2,3 for Arcade, Team, Endurance, Tournament."
    ),
    enum_labels=_TOP_LEVEL_MENU_CURSOR_LABELS,
)

MK4_ARCADE_PLAYER_COUNT_CURSOR = Mk4MemorySymbol(
    key="arcade_player_count_cursor",
    label="Arcade -> 1P/2P Cursor",
    virtual_address=0x8011D810,
    width_bytes=1,
    kind="u8_enum",
    description="Highlighted row on Arcade player-count screen (1 Player / 2 Player)",
    confidence=0.92,
    notes=(
        "Reuses the same underlying cursor byte as the top-level main menu (0x8011D810), "
        "but enum labels are screen-specific. Observed 0->1 when moving from 1 Player to 2 Player."
    ),
    enum_labels=_ARCADE_PLAYER_COUNT_CURSOR_LABELS,
)

MK4_MENU_SCREEN_STATE = Mk4MemorySymbol(
    key="menu_screen_state",
    label="Menu -> Screen State ID",
    virtual_address=0x80048D34,
    width_bytes=1,
    kind="u8_enum",
    description="Menu/screen state byte observed across top-level menu and Options screen",
    confidence=0.9,
    notes=(
        "Observed values so far: 0=Intro/Attract, 6=Top-Level Main Menu, "
        "10=Arcade Rumble Warning, 13=Options Menu, 38=Character Select. Top-level vs Options was isolated "
        "with reversible A==C filtering; intro and arcade rumble warning were captured "
        "explicitly from those screens and compared against menu snapshots."
    ),
    enum_labels=_MENU_SCREEN_STATE_LABELS,
)


_SYMBOLS: dict[str, Mk4MemorySymbol] = {
    MK4_ARCADE_PLAYER_COUNT_CURSOR.key: MK4_ARCADE_PLAYER_COUNT_CURSOR,
    MK4_MENU_SCREEN_STATE.key: MK4_MENU_SCREEN_STATE,
    MK4_TOP_LEVEL_MENU_CURSOR.key: MK4_TOP_LEVEL_MENU_CURSOR,
    MK4_OPTIONS_DIFFICULTY.key: MK4_OPTIONS_DIFFICULTY,
}


def mk4_symbol_registry() -> dict[str, Mk4MemorySymbol]:
    return dict(_SYMBOLS)


def normalize_options_difficulty(value: str | int | Mk4OptionsDifficulty) -> Mk4OptionsDifficulty:
    if isinstance(value, Mk4OptionsDifficulty):
        return value
    if isinstance(value, int):
        try:
            return Mk4OptionsDifficulty(value)
        except ValueError as exc:
            raise ValueError(f"Invalid MK4 options difficulty value: {value}") from exc
    text = str(value).strip().lower().replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    if text.isdigit():
        return normalize_options_difficulty(int(text))
    enum_value = _OPTIONS_DIFFICULTY_ALIASES.get(text)
    if enum_value is None:
        valid = ", ".join(sorted(set(_OPTIONS_DIFFICULTY_ALIASES)))
        raise ValueError(f"Unknown MK4 options difficulty label: {value!r}. Known labels: {valid}")
    return enum_value


def options_difficulty_label(value: int | Mk4OptionsDifficulty) -> str:
    try:
        enum_value = normalize_options_difficulty(int(value))
    except ValueError:
        return f"Unknown({int(value)})"
    return _OPTIONS_DIFFICULTY_LABELS[int(enum_value)]


def top_level_menu_cursor_label(value: int | Mk4TopLevelMenuCursor) -> str:
    try:
        enum_value = Mk4TopLevelMenuCursor(int(value))
    except ValueError:
        return f"Unknown({int(value)})"
    return _TOP_LEVEL_MENU_CURSOR_LABELS[int(enum_value)]


def arcade_player_count_cursor_label(value: int | Mk4ArcadePlayerCountCursor) -> str:
    try:
        enum_value = Mk4ArcadePlayerCountCursor(int(value))
    except ValueError:
        return f"Unknown({int(value)})"
    return _ARCADE_PLAYER_COUNT_CURSOR_LABELS[int(enum_value)]


def menu_screen_state_label(value: int | Mk4MenuScreenState) -> str:
    try:
        enum_value = Mk4MenuScreenState(int(value))
    except ValueError:
        return f"Unknown({int(value)})"
    return _MENU_SCREEN_STATE_LABELS[int(enum_value)]
