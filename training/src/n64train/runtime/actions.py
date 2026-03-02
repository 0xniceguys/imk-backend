from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
    NEUTRAL       = "NEUTRAL"        # no input — stand still
    ADVANCE       = "ADVANCE"        # walk toward opponent (D-RIGHT by default)
    RETREAT       = "RETREAT"        # walk away (D-LEFT by default)
    CROUCH        = "CROUCH"         # hold crouch (D-DOWN)
    JUMP_FORWARD  = "JUMP_FORWARD"   # D-UP + D-RIGHT
    JUMP_BACK     = "JUMP_BACK"      # D-UP + D-LEFT
    JUMP_NEUTRAL  = "JUMP_NEUTRAL"   # D-UP straight up
    SIDE_STEP_IN  = "SIDE_STEP_IN"   # L — sidestep toward screen
    SIDE_STEP_OUT = "SIDE_STEP_OUT"  # R — sidestep away from screen
    RUN           = "RUN"            # C-DOWN — dash/run toward opponent

    # ── Defense ───────────────────────────────
    STAND_BLOCK  = "STAND_BLOCK"     # C-LEFT (or Z) — standing block
    CROUCH_BLOCK = "CROUCH_BLOCK"    # D-DOWN + C-LEFT — low block

    # ── Attacks ───────────────────────────────
    LOW_PUNCH    = "LOW_PUNCH"       # A
    HIGH_PUNCH   = "HIGH_PUNCH"      # B
    LOW_KICK     = "LOW_KICK"        # C-RIGHT
    HIGH_KICK    = "HIGH_KICK"       # C-UP

    # ── Combo / pressure ──────────────────────
    JAB_COMBO    = "JAB_COMBO"       # A + C-RIGHT (LP+LK simultaneously)
    PUNISH       = "PUNISH"          # B + A (HP+LP together — punish opener)

    # ── Specials (character-dependent) ────────
    SPECIAL_1    = "SPECIAL_1"       # D-LEFT + A  (e.g. Scorpion spear)
    SPECIAL_2    = "SPECIAL_2"       # D-DOWN + D-LEFT + A  (e.g. teleport)

    # ── Utility ───────────────────────────────
    THROW_ATTEMPT = "THROW_ATTEMPT"  # close range: D-LEFT + A


@dataclass(frozen=True)
class ControllerState:
    # Analog axes: N64-style normalized in [-1.0, 1.0].
    analog_x: float = 0.0
    analog_y: float = 0.0
    pressed: frozenset[Button] = frozenset()

    def clipped(self) -> "ControllerState":
        return ControllerState(
            analog_x=max(-1.0, min(1.0, self.analog_x)),
            analog_y=max(-1.0, min(1.0, self.analog_y)),
            pressed=self.pressed,
        )
