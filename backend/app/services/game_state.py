"""
Game state reader — reads MK4 fight state from N64 RAM.

Self-contained: no imports from the training package.
Memory addresses confirmed via reverse engineering (see training/src/n64train/reverse/).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.bridge import EmulatorBridge, read_u8, read_u32

# ── Confirmed MK4 memory addresses (N64 virtual RDRAM) ──

P1_HEALTH_ADDR = 0x8036E729   # u8, 0=KO, 160=full
P2_HEALTH_ADDR = 0x8036E72E   # u8, same range
FIGHT_TIMER_ADDR = 0x80105118  # u8, counts down from 99
P1_X_ADDR = 0x800F87F8         # u32, position in upper halfword (signed i16)
P2_X_ADDR = 0x8006A060         # u32, position in upper halfword (signed i16)

# ── New combat signal addresses ──
P1_ACTION_ADDR = 0x800F8800    # u32, attack type indicator
P2_ACTION_ADDR = 0x8006A068    # u32, attack type indicator
P1_Y_VEL_ADDR = 0x800F87FC     # u32, Y velocity (signed i16 in upper halfword)
P1_HITSTUN_ADDR = 0x800F8808   # u32, hitstun flag
P2_HITSTUN_ADDR = 0x8006A070   # u32, hitstun flag
P1_AIRBORNE_ADDR = 0x800F880C  # u32, airborne flag
P2_AIRBORNE_ADDR = 0x8006A074  # u32, airborne flag

HEALTH_MAX = 160  # 0xA0


def _safe_u32(bridge: EmulatorBridge, addr: int) -> int:
    """Read u32 with fallback to 0 on error."""
    try:
        return read_u32(bridge, addr)
    except Exception:
        return 0


def _to_i32(val: int) -> int:
    """Convert unsigned 32-bit int to signed 32-bit int."""
    return val if val < 0x80000000 else val - 0x100000000


@dataclass
class FightState:
    """Snapshot of the current fight state read from RAM."""
    frame_id: int = 0
    p1_health: int = HEALTH_MAX
    p2_health: int = HEALTH_MAX
    timer: int = 99
    p1_x: float = 0.0
    p2_x: float = 0.0

    # New combat signals
    p1_action: float = 0.0      # attack type 0=idle, 0.2=LK, ~0.7+=others
    p2_action: float = 0.0
    p1_y_vel: float = 0.0       # Y velocity [-1,1]
    p2_airborne: float = 0.0    # {0,1}
    p1_hitstun: float = 0.0     # {0,1}
    p2_hitstun: float = 0.0     # {0,1}
    p1_airborne: float = 0.0    # {0,1}

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
            "p1_action": self.p1_action,
            "p2_action": self.p2_action,
            "p1_y_vel": self.p1_y_vel,
            "p2_airborne": self.p2_airborne,
            "p1_hitstun": self.p1_hitstun,
            "p2_hitstun": self.p2_hitstun,
            "p1_airborne": self.p1_airborne,
        }


def read_fight_state(bridge: EmulatorBridge, frame_id: int) -> FightState:
    """Read the current fight state from emulator RAM."""
    try:
        p1_hp = read_u8(bridge, P1_HEALTH_ADDR)
        p2_hp = read_u8(bridge, P2_HEALTH_ADDR)
        timer = read_u8(bridge, FIGHT_TIMER_ADDR)

        # Positions: upper 16 bits of 32-bit word, interpreted as signed int16
        p1_word = read_u32(bridge, P1_X_ADDR)
        p2_word = read_u32(bridge, P2_X_ADDR)
        p1_hi = (p1_word >> 16) & 0xFFFF
        p2_hi = (p2_word >> 16) & 0xFFFF
        p1_x = float(p1_hi if p1_hi < 0x8000 else p1_hi - 0x10000)
        p2_x = float(p2_hi if p2_hi < 0x8000 else p2_hi - 0x10000)

        # Combat signals
        p1_action_raw = _safe_u32(bridge, P1_ACTION_ADDR)
        p2_action_raw = _safe_u32(bridge, P2_ACTION_ADDR)
        p1_y_vel_raw = _safe_u32(bridge, P1_Y_VEL_ADDR)
        p1_hitstun_raw = _safe_u32(bridge, P1_HITSTUN_ADDR)
        p2_hitstun_raw = _safe_u32(bridge, P2_HITSTUN_ADDR)
        p1_airborne_raw = _safe_u32(bridge, P1_AIRBORNE_ADDR)
        p2_airborne_raw = _safe_u32(bridge, P2_AIRBORNE_ADDR)

        # Normalize action (0=idle, 0.2=LK, ~0.7+=specials)
        p1_action = min(1.0, float(p1_action_raw) / 100.0)
        p2_action = min(1.0, float(p2_action_raw) / 100.0)

        # Y velocity: upper 16 bits as signed i16, normalized to [-1,1]
        p1_y_vel_i16 = (p1_y_vel_raw >> 16) & 0xFFFF
        p1_y_vel_signed = p1_y_vel_i16 if p1_y_vel_i16 < 0x8000 else p1_y_vel_i16 - 0x10000
        p1_y_vel = max(-1.0, min(1.0, float(p1_y_vel_signed) / 32768.0))

        # Binary flags
        p1_hitstun = 1.0 if p1_hitstun_raw != 0 else 0.0
        p2_hitstun = 1.0 if p2_hitstun_raw != 0 else 0.0
        p1_airborne = 1.0 if p1_airborne_raw != 0 else 0.0
        p2_airborne = 1.0 if p2_airborne_raw != 0 else 0.0

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
        p1_action=p1_action,
        p2_action=p2_action,
        p1_y_vel=p1_y_vel,
        p2_airborne=p2_airborne,
        p1_hitstun=p1_hitstun,
        p2_hitstun=p2_hitstun,
        p1_airborne=p1_airborne,
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
