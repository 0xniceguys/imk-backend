"""
Game state reader — reads MK4 fight state from N64 RAM.

Self-contained: no imports from the training package.
Memory addresses confirmed via reverse engineering (see training/src/n64train/reverse/).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.bridge import EmulatorBridge, read_u8, read_u32

# ── Confirmed MK4 memory addresses (N64 virtual RDRAM) ──

P1_HEALTH_ADDR = 0x800FE0D8   # u32 fixed-point, full health = 0x00010000
P2_HEALTH_ADDR = 0x80126F54   # u32 fixed-point, same scale
FIGHT_TIMER_ADDR = 0x80105118  # u8, counts down from 99
P1_X_ADDR = 0x800F87F8         # u32, position in upper halfword (signed i16)
P2_X_ADDR = 0x8006A060         # u32, position in upper halfword (signed i16)

HEALTH_MAX = 160  # 0xA0
HEALTH_FP_ONE = 0x00010000


@dataclass
class FightState:
    """Snapshot of the current fight state read from RAM."""
    frame_id: int = 0
    p1_health: int = HEALTH_MAX
    p2_health: int = HEALTH_MAX
    timer: int = 99
    p1_x: float = 0.0
    p2_x: float = 0.0

    def to_dict(self) -> dict:
        return {
            "type": "game_state",
            "frame_id": self.frame_id,
            "p1_health": self.p1_health,
            "p2_health": self.p2_health,
            "p1_health_pct": round(self.p1_health / HEALTH_MAX, 3) if HEALTH_MAX else 0,
            "p2_health_pct": round(self.p2_health / HEALTH_MAX, 3) if HEALTH_MAX else 0,
            "timer": self.timer,
            "p1_x": self.p1_x,
            "p2_x": self.p2_x,
        }


def read_fight_state(bridge: EmulatorBridge, frame_id: int) -> FightState:
    """Read the current fight state from emulator RAM."""
    try:
        p1_word = read_u32(bridge, P1_HEALTH_ADDR)
        p2_word = read_u32(bridge, P2_HEALTH_ADDR)
        p1_hp = int(round(max(0.0, min(1.0, p1_word / HEALTH_FP_ONE)) * HEALTH_MAX))
        p2_hp = int(round(max(0.0, min(1.0, p2_word / HEALTH_FP_ONE)) * HEALTH_MAX))
        timer = read_u8(bridge, FIGHT_TIMER_ADDR)

        # Positions: upper 16 bits of 32-bit word, interpreted as signed int16
        p1_word = read_u32(bridge, P1_X_ADDR)
        p2_word = read_u32(bridge, P2_X_ADDR)
        p1_hi = (p1_word >> 16) & 0xFFFF
        p2_hi = (p2_word >> 16) & 0xFFFF
        p1_x = float(p1_hi if p1_hi < 0x8000 else p1_hi - 0x10000)
        p2_x = float(p2_hi if p2_hi < 0x8000 else p2_hi - 0x10000)
    except Exception:
        # On read failure, return stub so the loop doesn't crash
        return FightState(frame_id=frame_id)

    return FightState(
        frame_id=frame_id,
        p1_health=p1_hp,
        p2_health=p2_hp,
        timer=timer,
        p1_x=p1_x,
        p2_x=p2_x,
    )


def is_round_over(state: FightState) -> bool:
    """True when someone is KO'd or the timer runs out.

    NOTE: This function is only called after the 300-step grace period in
    match_runner.py (~30s), so we don't need extra guards here.
    The only safe guard is both-zero which means uninitialized RAM.
    """
    p1, p2, timer = state.p1_health, state.p2_health, state.timer

    # Both zero → uninitialized RAM (shouldn't happen after grace period)
    if p1 == 0 and p2 == 0:
        return False

    # KO — one player's health hit zero
    if p1 <= 0 or p2 <= 0:
        return True

    # Timer expired — round ends, highest health wins
    if timer <= 0:
        return True

    return False


def p1_won(state: FightState) -> bool:
    """True when P1 wins the round (KO or timer expiry)."""
    p1, p2 = state.p1_health, state.p2_health
    if p1 == 0 and p2 == 0:
        return False
    # P1 KO'd the opponent
    if p2 <= 0 and p1 > 0:
        return True
    # P2 KO'd P1
    if p1 <= 0 and p2 > 0:
        return False
    # Timer expired — higher health wins (P1 wins ties)
    if state.timer <= 0:
        return p1 >= p2
    return False
