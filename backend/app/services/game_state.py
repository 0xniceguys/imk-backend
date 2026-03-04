"""
Game state reader — reads MK4 fight state from N64 RAM.

Self-contained: no imports from the training package.
Memory addresses confirmed via live RAM differential scan (2026-03-03)
and validated against running emulator (2026-03-04).
See training/src/n64train/reverse/mk4_tracing.py for full address documentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.bridge import EmulatorBridge, read_u8, read_u32

# ── Confirmed MK4 memory addresses (N64 virtual RDRAM) ──────────────────────
# ALL addresses verified by live emulator validation (validate_addresses.py).
# Canonical source: training/src/n64train/reverse/mk4_tracing.py

# Health (u32, 16.16 fixed-point: 0x10000 = 65536 = full, 0 = dead)
P1_HEALTH_ADDR   = 0x800FE0D8   # u32: internal health, per-hit updates
P2_HEALTH_ADDR   = 0x80126F54   # u32: internal health, per-hit updates
FIGHT_TIMER_ADDR = 0x80105118   # u8: counts down from 99

# Positions: upper 16 bits of u32, interpreted as signed i16
P1_X_ADDR = 0x800F87F8          # u32: hi-halfword = P1 X position
P2_X_ADDR = 0x8006A060          # u32: hi-halfword = P2 X position

# ── Combat signal addresses (verified by live differential scan 2026-03-03) ──
# P1 attack type: LP=69422, HP=67956, HK=68606, idle=0
P1_ATTACK_TYPE_ADDR = 0x800FE090  # u32: attack type register
P1_LK_ADDR          = 0x800FE144  # u32: changes ONLY on LK (idle=0x8011E2C0)
P1_GROUND_FLAG_ADDR = 0x800FE0F8  # u32: 4=on_ground, 1=airborne
P1_Y_VEL_ADDR       = 0x800FE90C  # u32: PERFECT ARC on jump (signed 32-bit)
P1_HITSTUN_ADDR     = 0x800FE310  # u32: 0=idle, non-zero during HP or block

# P2 equivalents — DIFFERENT offsets from P2 base (0x80126E00)
P2_ATTACK_TYPE_ADDR = 0x80126E94  # u32: LP=-91881, HP=-87293, HK=-129236
P2_LK_ADDR          = 0x80126F30  # u32: idle=2946, LK=3131, LP=3139, HK=3133
P2_GROUND_FLAG_ADDR = 0x80126ECC  # u32: 2=ground, 1=airborne
P2_HITSTUN_ADDR     = 0x80126F9C  # u32: 0=idle, 2=punch active

HEALTH_RAW_MAX = 0x10000  # 65536
HEALTH_MAX = 160  # 0xA0 — normalized display scale


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
        # Health: u32 in 16.16 fixed-point (0x10000=65536=full, 0=dead)
        # NOTE: no clamping — clamping garbage reads (e.g. 0xC0C00000) to 0x10000
        # was masking bad addresses by making HP always appear as 160.
        p1_hp_raw = read_u32(bridge, P1_HEALTH_ADDR)
        p2_hp_raw = read_u32(bridge, P2_HEALTH_ADDR)

        p1_hp = int(p1_hp_raw * HEALTH_MAX / HEALTH_RAW_MAX) if p1_hp_raw <= HEALTH_RAW_MAX else HEALTH_MAX
        p2_hp = int(p2_hp_raw * HEALTH_MAX / HEALTH_RAW_MAX) if p2_hp_raw <= HEALTH_RAW_MAX else HEALTH_MAX

        timer = read_u8(bridge, FIGHT_TIMER_ADDR)

        # Positions: upper 16 bits of 32-bit word, interpreted as signed int16
        p1_word = read_u32(bridge, P1_X_ADDR)
        p2_word = read_u32(bridge, P2_X_ADDR)
        p1_hi = (p1_word >> 16) & 0xFFFF
        p2_hi = (p2_word >> 16) & 0xFFFF
        p1_x = float(p1_hi if p1_hi < 0x8000 else p1_hi - 0x10000)
        p2_x = float(p2_hi if p2_hi < 0x8000 else p2_hi - 0x10000)

        # ── Combat signals (same addresses + normalization as mk4_tracing.py) ──

        # P1 attack type: 5-class via attack_type + LK register
        # P1_LK_IDLE confirmed via live scan 2026-03-03 (was 0x8011E2C0, now 0x80126B20)
        P1_LK_IDLE = 0x80126B20
        p1_atk_raw = _safe_u32(bridge, P1_ATTACK_TYPE_ADDR)
        p1_lk_raw  = _safe_u32(bridge, P1_LK_ADDR)
        if p1_atk_raw != 0:
            p1_action = min(1.0, float(p1_atk_raw) / 70000.0)
        elif p1_lk_raw != P1_LK_IDLE:
            p1_action = 0.2  # LK
        else:
            p1_action = 0.0

        # P2 attack type: primary (LP/HP/HK) + secondary (LK detection)
        P2_LK_IDLE = 2946
        p2_atk_raw = _safe_u32(bridge, P2_ATTACK_TYPE_ADDR)
        p2_lk_raw  = _safe_u32(bridge, P2_LK_ADDR)
        if p2_atk_raw != 0:
            p2_action = min(1.0, abs(_to_i32(p2_atk_raw)) / 130000.0)
        elif p2_lk_raw != P2_LK_IDLE:
            p2_action = 0.2  # LK
        else:
            p2_action = 0.0

        # Y velocity: signed 32-bit, normalize to [-1, +1]
        p1_y_vel_raw = _safe_u32(bridge, P1_Y_VEL_ADDR)
        s32 = _to_i32(p1_y_vel_raw)
        p1_y_vel = max(-1.0, min(1.0, s32 / 100000.0))

        # Ground flags → airborne detection
        # P1: 4=ground, 1=airborne; P2: 2=ground, 1=airborne
        p1_gnd_raw = _safe_u32(bridge, P1_GROUND_FLAG_ADDR)
        p2_gnd_raw = _safe_u32(bridge, P2_GROUND_FLAG_ADDR)
        p1_airborne = 1.0 if p1_gnd_raw == 1 else 0.0
        p2_airborne = 1.0 if p2_gnd_raw == 1 else 0.0

        # Hitstun: non-zero during active attack/block frames
        p1_hitstun_raw = _safe_u32(bridge, P1_HITSTUN_ADDR)
        p2_hitstun_raw = _safe_u32(bridge, P2_HITSTUN_ADDR)
        p1_hitstun = 1.0 if p1_hitstun_raw != 0 else 0.0
        p2_hitstun = 1.0 if p2_hitstun_raw != 0 else 0.0

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

    Guards mirror training/mk4_tracing.py to avoid false triggers:
    - Both zero → uninitialized RAM
    - Both at HEALTH_MAX (160) → savestate just loaded / RAM not yet populated
    - timer == 0 (exact) → round timer has expired (use == not <=, avoids negative timer reads)
    """
    p1, p2, timer = state.p1_health, state.p2_health, state.timer

    # Both zero → uninitialized RAM
    if p1 == 0 and p2 == 0:
        return False

    # Both at full HP → savestate just loaded, fight hasn't started yet
    if p1 == HEALTH_MAX and p2 == HEALTH_MAX:
        return False

    # One side at max while other is 0 → RAM still initializing
    if (p1 == HEALTH_MAX and p2 == 0) or (p2 == HEALTH_MAX and p1 == 0):
        return False

    # KO — one player's health hit zero
    if p1 <= 0 or p2 <= 0:
        return True

    # Timer expired — exact zero (not <=) to avoid negative garbage reads
    if timer == 0:
        return True

    return False


def p1_won(state: FightState) -> bool:
    """True when P1 wins the round (KO or timer expiry). Mirrors mk4_tracing.py."""
    p1, p2 = state.p1_health, state.p2_health
    # Guard: uninitialized RAM
    if p1 == 0 and p2 == 0:
        return False
    # P2 KO'd (health at or below zero)
    if p2 <= 0:
        return True
    # Timer-based win: higher HP wins (P1 wins ties)
    if state.timer == 0:
        return p1 >= p2
    return False
