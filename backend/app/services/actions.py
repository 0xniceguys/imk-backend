"""
Controller actions — N64 button mapping and macro-action system.

Self-contained: no imports from the training package.
Mirrors training/src/n64train/runtime/actions.py + codec logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Button(str, Enum):
    A = "A"
    B = "B"
    START = "START"
    Z = "Z"
    L = "L"
    R = "R"
    D_UP = "D_UP"
    D_DOWN = "D_DOWN"
    D_LEFT = "D_LEFT"
    D_RIGHT = "D_RIGHT"
    C_UP = "C_UP"
    C_DOWN = "C_DOWN"
    C_LEFT = "C_LEFT"
    C_RIGHT = "C_RIGHT"


# MK4 N64 button → game action mapping (from in-game Configure Controller screen):
#   A       = LOW PUNCH
#   B       = HIGH PUNCH
#   C-LEFT  = BLOCK  (same as Z)
#   C-UP    = HIGH KICK
#   C-RIGHT = LOW KICK
#   C-DOWN  = RUN
#   Z       = BLOCK
#   L       = SIDE STEP IN
#   R       = SIDE STEP OUT
#   D-LEFT  = Walk/retreat left
#   D-RIGHT = Walk/advance right
#   D-UP    = Jump
#   D-DOWN  = Crouch


class MacroAction(str, Enum):
    # ── Movement ──────────────────────────────
    NEUTRAL       = "NEUTRAL"
    ADVANCE       = "ADVANCE"
    RETREAT       = "RETREAT"
    CROUCH        = "CROUCH"
    JUMP_FORWARD  = "JUMP_FORWARD"
    JUMP_BACK     = "JUMP_BACK"
    JUMP_NEUTRAL  = "JUMP_NEUTRAL"
    SIDE_STEP_IN  = "SIDE_STEP_IN"
    SIDE_STEP_OUT = "SIDE_STEP_OUT"
    RUN           = "RUN"

    # ── Defense ───────────────────────────────
    STAND_BLOCK  = "STAND_BLOCK"
    CROUCH_BLOCK = "CROUCH_BLOCK"

    # ── Attacks ───────────────────────────────
    LOW_PUNCH    = "LOW_PUNCH"
    HIGH_PUNCH   = "HIGH_PUNCH"
    LOW_KICK     = "LOW_KICK"
    HIGH_KICK    = "HIGH_KICK"

    # ── Combo / pressure ──────────────────────
    JAB_COMBO    = "JAB_COMBO"
    PUNISH       = "PUNISH"

    # ── Specials (character-dependent) ────────
    SPECIAL_1    = "SPECIAL_1"
    SPECIAL_2    = "SPECIAL_2"

    # ── Utility ───────────────────────────────
    THROW_ATTEMPT = "THROW_ATTEMPT"


@dataclass(frozen=True)
class ControllerState:
    """N64 controller state: analog stick + digital buttons."""
    analog_x: float = 0.0
    analog_y: float = 0.0
    pressed: frozenset[Button] = frozenset()

    def clipped(self) -> ControllerState:
        return ControllerState(
            analog_x=max(-1.0, min(1.0, self.analog_x)),
            analog_y=max(-1.0, min(1.0, self.analog_y)),
            pressed=self.pressed,
        )


@dataclass(frozen=True)
class ActionPacket:
    """A single action to send to the emulator."""
    macro_action: MacroAction | None = None
    micro_controller_state: ControllerState = field(default_factory=ControllerState)
    repeat_frames: int = 1
    player: int = 1  # 1 = P1, 2 = P2

    def __post_init__(self) -> None:
        if self.repeat_frames < 1:
            raise ValueError("repeat_frames must be >= 1")
        if self.player not in (1, 2):
            raise ValueError("player must be 1 or 2")


# ── Macro → ControllerState mapping ──
# Maps each MacroAction to the buttons it presses.
# "Advance" = D-RIGHT for P1 (facing right), but in MK4 the game
# handles facing automatically, so D-RIGHT always moves right.

MACRO_TO_CONTROLLER: dict[MacroAction, ControllerState] = {
    MacroAction.NEUTRAL: ControllerState(),
    MacroAction.ADVANCE: ControllerState(pressed=frozenset({Button.D_RIGHT})),
    MacroAction.RETREAT: ControllerState(pressed=frozenset({Button.D_LEFT})),
    MacroAction.CROUCH: ControllerState(pressed=frozenset({Button.D_DOWN})),
    MacroAction.JUMP_FORWARD: ControllerState(pressed=frozenset({Button.D_UP, Button.D_RIGHT})),
    MacroAction.JUMP_BACK: ControllerState(pressed=frozenset({Button.D_UP, Button.D_LEFT})),
    MacroAction.JUMP_NEUTRAL: ControllerState(pressed=frozenset({Button.D_UP})),
    MacroAction.SIDE_STEP_IN: ControllerState(pressed=frozenset({Button.L})),
    MacroAction.SIDE_STEP_OUT: ControllerState(pressed=frozenset({Button.R})),
    MacroAction.RUN: ControllerState(pressed=frozenset({Button.C_DOWN, Button.D_RIGHT})),
    MacroAction.STAND_BLOCK: ControllerState(pressed=frozenset({Button.C_LEFT})),
    MacroAction.CROUCH_BLOCK: ControllerState(pressed=frozenset({Button.D_DOWN, Button.C_LEFT})),
    MacroAction.LOW_PUNCH: ControllerState(pressed=frozenset({Button.A})),
    MacroAction.HIGH_PUNCH: ControllerState(pressed=frozenset({Button.B})),
    MacroAction.LOW_KICK: ControllerState(pressed=frozenset({Button.C_RIGHT})),
    MacroAction.HIGH_KICK: ControllerState(pressed=frozenset({Button.C_UP})),
    MacroAction.JAB_COMBO: ControllerState(pressed=frozenset({Button.A, Button.C_RIGHT})),
    MacroAction.PUNISH: ControllerState(pressed=frozenset({Button.B, Button.A})),
    MacroAction.SPECIAL_1: ControllerState(pressed=frozenset({Button.D_LEFT, Button.A})),
    MacroAction.SPECIAL_2: ControllerState(pressed=frozenset({Button.D_DOWN, Button.D_LEFT, Button.A})),
    # THROW_ATTEMPT: forward throw = D-RIGHT + LOW PUNCH (distinct from SPECIAL_1 = D-LEFT+A)
    MacroAction.THROW_ATTEMPT: ControllerState(pressed=frozenset({Button.D_RIGHT, Button.A})),
}

# P2 mirrored button map (right-side player gets reversed directions)
MACRO_TO_CONTROLLER_P2: dict[MacroAction, ControllerState] = {
    MacroAction.NEUTRAL: ControllerState(),
    MacroAction.ADVANCE: ControllerState(pressed=frozenset({Button.D_LEFT})),
    MacroAction.RETREAT: ControllerState(pressed=frozenset({Button.D_RIGHT})),
    MacroAction.CROUCH: ControllerState(pressed=frozenset({Button.D_DOWN})),
    MacroAction.JUMP_FORWARD: ControllerState(pressed=frozenset({Button.D_UP, Button.D_LEFT})),
    MacroAction.JUMP_BACK: ControllerState(pressed=frozenset({Button.D_UP, Button.D_RIGHT})),
    MacroAction.JUMP_NEUTRAL: ControllerState(pressed=frozenset({Button.D_UP})),
    MacroAction.SIDE_STEP_IN: ControllerState(pressed=frozenset({Button.L})),
    MacroAction.SIDE_STEP_OUT: ControllerState(pressed=frozenset({Button.R})),
    MacroAction.RUN: ControllerState(pressed=frozenset({Button.C_DOWN, Button.D_LEFT})),
    MacroAction.STAND_BLOCK: ControllerState(pressed=frozenset({Button.C_LEFT})),
    MacroAction.CROUCH_BLOCK: ControllerState(pressed=frozenset({Button.D_DOWN, Button.C_LEFT})),
    MacroAction.LOW_PUNCH: ControllerState(pressed=frozenset({Button.A})),
    MacroAction.HIGH_PUNCH: ControllerState(pressed=frozenset({Button.B})),
    MacroAction.LOW_KICK: ControllerState(pressed=frozenset({Button.C_RIGHT})),
    MacroAction.HIGH_KICK: ControllerState(pressed=frozenset({Button.C_UP})),
    MacroAction.JAB_COMBO: ControllerState(pressed=frozenset({Button.A, Button.C_RIGHT})),
    MacroAction.PUNISH: ControllerState(pressed=frozenset({Button.B, Button.A})),
    MacroAction.SPECIAL_1: ControllerState(pressed=frozenset({Button.D_RIGHT, Button.A})),
    MacroAction.SPECIAL_2: ControllerState(pressed=frozenset({Button.D_DOWN, Button.D_RIGHT, Button.A})),
    MacroAction.THROW_ATTEMPT: ControllerState(pressed=frozenset({Button.D_LEFT, Button.A})),
}


def resolve_action(packet: ActionPacket) -> ActionPacket:
    """Resolve a macro action into concrete controller state if needed.

    If the packet already has micro_controller_state with pressed buttons,
    returns as-is. Otherwise maps the macro_action to buttons.
    """
    if packet.micro_controller_state.pressed:
        return packet

    if packet.macro_action is None:
        return packet

    button_map = MACRO_TO_CONTROLLER_P2 if packet.player == 2 else MACRO_TO_CONTROLLER
    ctrl = button_map.get(packet.macro_action, ControllerState())
    return ActionPacket(
        macro_action=packet.macro_action,
        micro_controller_state=ctrl,
        repeat_frames=packet.repeat_frames,
        player=packet.player,
    )


# ── Codec: serialize/deserialize for bridge JSON protocol ──

def encode_controller_state(state: ControllerState) -> dict[str, Any]:
    return {
        "analog_x": state.analog_x,
        "analog_y": state.analog_y,
        "pressed": [button.value for button in sorted(state.pressed, key=lambda b: b.value)],
    }


def decode_controller_state(payload: dict[str, Any]) -> ControllerState:
    return ControllerState(
        analog_x=float(payload.get("analog_x", 0.0)),
        analog_y=float(payload.get("analog_y", 0.0)),
        pressed=frozenset(Button(str(v)) for v in payload.get("pressed", [])),
    )


def encode_action_packet(packet: ActionPacket) -> dict[str, Any]:
    return {
        "macro_action": packet.macro_action.value if packet.macro_action is not None else None,
        "micro_controller_state": encode_controller_state(packet.micro_controller_state),
        "repeat_frames": packet.repeat_frames,
        "player": packet.player,
    }


def decode_action_packet(payload: dict[str, Any]) -> ActionPacket:
    macro_raw = payload.get("macro_action")
    return ActionPacket(
        macro_action=MacroAction(str(macro_raw)) if macro_raw is not None else None,
        micro_controller_state=decode_controller_state(
            dict(payload.get("micro_controller_state", {}))
        ),
        repeat_frames=int(payload.get("repeat_frames", 1)),
        player=int(payload.get("player", 1)),
    )
