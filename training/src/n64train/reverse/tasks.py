from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReverseTask:
    task_id: str
    title: str
    goal: str
    labels: tuple[str, ...]
    notes: tuple[str, ...]


def default_reverse_tasks() -> tuple[ReverseTask, ...]:
    return (
        ReverseTask(
            task_id="menu_character_select",
            title="Character Select State Discovery",
            goal="Find menu/select screen state, cursor index/position, and selected character IDs for P1/P2.",
            labels=(
                "boot_menu_idle",
                "character_select_cursor_start",
                "character_select_cursor_move_left",
                "character_select_cursor_move_right",
                "character_select_p1_confirm",
                "character_select_p2_confirm",
            ),
            notes=(
                "Capture pairs where only one control input changes between snapshots.",
                "Use multiple characters to isolate stable ID fields from transient animation bytes.",
                "Record same label twice to estimate background noise.",
            ),
        ),
        ReverseTask(
            task_id="difficulty_setting",
            title="CPU Difficulty State Discovery",
            goal="Find difficulty menu state and value backing the maximum CPU setting.",
            labels=(
                "options_menu_idle",
                "difficulty_low",
                "difficulty_mid",
                "difficulty_max",
                "difficulty_decrement_once",
                "difficulty_increment_once",
            ),
            notes=(
                "Capture monotonic changes one step at a time.",
                "Diff adjacent levels before diffing low vs max.",
                "Look for small-width integers (8/16-bit) with small deltas.",
            ),
        ),
        ReverseTask(
            task_id="in_match_core_state",
            title="In-Match Core Trace Discovery",
            goal="Find P1/P2 positions, health, timer, and facing values.",
            labels=(
                "match_round_start_idle",
                "p1_walk_forward",
                "p1_walk_back",
                "p2_walk_forward",
                "p1_take_damage_small",
                "p2_take_damage_small",
                "timer_tick",
                "turnaround_facing_swap",
            ),
            notes=(
                "Use savestates to branch from same starting position.",
                "Capture repeated single-step movements for stable position deltas.",
                "Damage and timer labels should be captured in short consecutive sequences.",
            ),
        ),
    )
