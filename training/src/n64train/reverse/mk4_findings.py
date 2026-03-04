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

MK4_FIGHT_TIMER = Mk4MemorySymbol(
    key="fight_timer",
    label="Fight -> Round Timer",
    virtual_address=0x80105118,
    width_bytes=1,
    kind="u8",
    description="Round timer byte during active fights",
    confidence=0.98,
    notes=(
        "Repeated live probes show a clean countdown from 99 toward 0 in fight states. "
        "Used directly by the current tracer."
    ),
)

MK4_P1_HEALTH_WORD = Mk4MemorySymbol(
    key="p1_health_word",
    label="Fight -> P1 Internal Health Word",
    virtual_address=0x800FE0D8,
    width_bytes=4,
    kind="u32_fixed16_16_health160",
    description="P1 internal 16.16 health word; 0x00010000 decodes to 160 HP",
    confidence=0.99,
    notes=(
        "Matches the local GameShark infinite-health word and repeated deterministic live "
        "probes on both p1p2state.st and arcade_training_scorpion.st."
    ),
)

MK4_P2_HEALTH_WORD = Mk4MemorySymbol(
    key="p2_health_word",
    label="Fight -> P2 Internal Health Word",
    virtual_address=0x80126F54,
    width_bytes=4,
    kind="u32_fixed16_16_health160",
    description="P2 internal 16.16 health word; 0x00010000 decodes to 160 HP",
    confidence=0.99,
    notes=(
        "Matches the local GameShark infinite-health word and repeated deterministic live "
        "probes on both p1p2state.st and arcade_training_scorpion.st."
    ),
)

MK4_P1_X_POSITION = Mk4MemorySymbol(
    key="p1_x_position",
    label="Fight -> P1 X Position",
    virtual_address=0x800F87F8,
    width_bytes=2,
    kind="s16hi",
    description="P1 X position stored in the upper signed halfword of a 32-bit word",
    confidence=0.95,
    notes=(
        "Confirmed by monotonic left/right movement scans and used directly by the current tracer."
    ),
)

MK4_P2_X_POSITION = Mk4MemorySymbol(
    key="p2_x_position",
    label="Fight -> P2 X Position",
    virtual_address=0x8006A060,
    width_bytes=2,
    kind="s16hi",
    description="P2 X position stored in the upper signed halfword of a 32-bit word",
    confidence=0.95,
    notes=(
        "Confirmed by CPU idle-walk and monotonic movement scans and used directly by the current tracer."
    ),
)

MK4_P1_GROUND_FLAG = Mk4MemorySymbol(
    key="p1_ground_flag",
    label="Fight -> P1 Ground/Air Flag",
    virtual_address=0x800FE0F8,
    width_bytes=4,
    kind="u32_flag",
    description="Candidate P1 ground/air flag; observed 4=ground and 1=airborne",
    confidence=0.85,
    notes=(
        "Repeated deterministic jump probes show it flipping from 4 to 1 during a P1 jump. "
        "Used as the current P1 airborne signal."
    ),
)

MK4_P2_GROUND_FLAG_CANDIDATE = Mk4MemorySymbol(
    key="p2_ground_flag_candidate",
    label="Fight -> P2 Ground/Air Candidate",
    virtual_address=0x80126F78,
    width_bytes=2,
    kind="s16hi",
    description="P2 jump/air indicator in the upper halfword; 0 on ground and non-zero during P2 jump",
    confidence=0.86,
    notes=(
        "Repeated deterministic probes show the upper halfword staying at 0 through neutral and "
        "P1-only actions, then flipping to 3068 (0x0BFC) during P2 jump frames. This is the "
        "current traced P2 airborne signal."
    ),
)

MK4_P1_Y_VELOCITY = Mk4MemorySymbol(
    key="p1_y_velocity",
    label="Fight -> P1 Y Velocity",
    virtual_address=0x800FE90C,
    width_bytes=4,
    kind="s32",
    description="P1 Y velocity / vertical motion word",
    confidence=0.87,
    notes=(
        "Deterministic jump probes show a clean sign-changing arc: idle positive baseline, then "
        "negative while rising. Used as the current P1 vertical-motion signal."
    ),
)

MK4_P1_ATTACK_PRIMARY = Mk4MemorySymbol(
    key="p1_attack_primary",
    label="Fight -> P1 Primary Attack Register",
    virtual_address=0x800FE090,
    width_bytes=4,
    kind="s32",
    description="Primary P1 attack register used as a conservative attack-active signal",
    confidence=0.4,
    notes=(
        "Deterministic probes show clean non-zero values for some attacks such as high punch, "
        "but the register can also change during the opponent's punch phase. It is currently a candidate only, "
        "not a trusted player-isolated action feature."
    ),
)

MK4_P2_ATTACK_PRIMARY = Mk4MemorySymbol(
    key="p2_attack_primary",
    label="Fight -> P2 Primary Attack Register",
    virtual_address=0x80126E94,
    width_bytes=4,
    kind="s32",
    description="Primary P2 attack register used as a conservative attack-active signal",
    confidence=0.4,
    notes=(
        "Deterministic probes show clean non-zero values for some attacks such as high punch, "
        "but the register can also change during the opponent's punch phase. It is currently a candidate only, "
        "not a trusted player-isolated action feature."
    ),
)

MK4_P1_LK_CANDIDATE = Mk4MemorySymbol(
    key="p1_lk_candidate",
    label="Fight -> P1 Low-Kick Candidate Register",
    virtual_address=0x800FE144,
    width_bytes=4,
    kind="u32",
    description="Reverse-engineering candidate for a P1 low-kick side register",
    confidence=0.25,
    notes=(
        "Drifts at idle in deterministic live probes, so it is not safe to use as a traced feature yet."
    ),
)

MK4_P2_LK_CANDIDATE = Mk4MemorySymbol(
    key="p2_lk_candidate",
    label="Fight -> P2 Low-Kick Candidate Register",
    virtual_address=0x80126F30,
    width_bytes=4,
    kind="u32",
    description="Reverse-engineering candidate for a P2 low-kick side register",
    confidence=0.2,
    notes=(
        "Drifts steadily during neutral in deterministic live probes, so it is not safe to use as a traced feature yet."
    ),
)

MK4_P1_HITBOX_CANDIDATE = Mk4MemorySymbol(
    key="p1_hitbox_candidate",
    label="Fight -> P1 Hitbox/Block Candidate",
    virtual_address=0x800FE310,
    width_bytes=4,
    kind="u32",
    description="Early-scan candidate for a P1 attack/block side signal",
    confidence=0.3,
    notes=(
        "Retained for reverse-engineering notes only. Deterministic probes did not yet show a stable "
        "idle->active->idle pattern across the training states."
    ),
)

MK4_P2_HITBOX_CANDIDATE = Mk4MemorySymbol(
    key="p2_hitbox_candidate",
    label="Fight -> P2 Hitbox/Block Candidate",
    virtual_address=0x80126F9C,
    width_bytes=4,
    kind="u32",
    description="Early-scan candidate for a P2 attack/block side signal",
    confidence=0.15,
    notes=(
        "Currently reads non-zero at idle in deterministic probes, so it is not trustworthy as a traced feature."
    ),
)


_SYMBOLS: dict[str, Mk4MemorySymbol] = {
    MK4_FIGHT_TIMER.key: MK4_FIGHT_TIMER,
    MK4_P1_ATTACK_PRIMARY.key: MK4_P1_ATTACK_PRIMARY,
    MK4_P1_GROUND_FLAG.key: MK4_P1_GROUND_FLAG,
    MK4_P1_HEALTH_WORD.key: MK4_P1_HEALTH_WORD,
    MK4_P1_HITBOX_CANDIDATE.key: MK4_P1_HITBOX_CANDIDATE,
    MK4_P1_LK_CANDIDATE.key: MK4_P1_LK_CANDIDATE,
    MK4_P1_X_POSITION.key: MK4_P1_X_POSITION,
    MK4_P1_Y_VELOCITY.key: MK4_P1_Y_VELOCITY,
    MK4_P2_ATTACK_PRIMARY.key: MK4_P2_ATTACK_PRIMARY,
    MK4_P2_GROUND_FLAG_CANDIDATE.key: MK4_P2_GROUND_FLAG_CANDIDATE,
    MK4_P2_HEALTH_WORD.key: MK4_P2_HEALTH_WORD,
    MK4_P2_HITBOX_CANDIDATE.key: MK4_P2_HITBOX_CANDIDATE,
    MK4_P2_LK_CANDIDATE.key: MK4_P2_LK_CANDIDATE,
    MK4_P2_X_POSITION.key: MK4_P2_X_POSITION,
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
