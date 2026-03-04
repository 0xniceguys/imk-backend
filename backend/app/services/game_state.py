"""
Game state reader — reads MK4 fight state from the emulator bridge.

Self-contained: no imports from the training package.
Prefers the bridge's canonical MK4 state contract when available and falls
back to direct debugger RAM reads for the verified core fight-state symbols.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.bridge import EmulatorBridge, read_u8, read_u32

# ── Canonical MK4 memory addresses (N64 virtual RDRAM) ──────────────────────

P1_HEALTH_ADDR = 0x800FE0D8   # u32 fixed-point, full health = 0x00010000
P2_HEALTH_ADDR = 0x80126F54   # u32 fixed-point, same scale
FIGHT_TIMER_ADDR = 0x80105118  # u8, counts down from 99
P1_X_ADDR = 0x800F87F8         # u32, position in upper halfword (signed i16)
P2_X_ADDR = 0x8006A060         # u32, position in upper halfword (signed i16)
P1_GROUND_FLAG_ADDR = 0x800FE0F8  # u32: 4=ground, 1=airborne
P2_AIR_FLAG_ADDR = 0x80126F78     # u32: hi16 == 0 on ground, non-zero during P2 jump
P1_Y_VEL_ADDR = 0x800FE90C        # u32 signed vertical velocity

HEALTH_MAX = 160
HEALTH_FP_ONE = 0x00010000
Y_VEL_NORM = 100000.0
MK4_STATE_CONTRACT_VERSION = "mk4_core_v1"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _decode_health_word(word: int) -> int:
    return int(round(_clamp01(float(word) / float(HEALTH_FP_ONE)) * HEALTH_MAX))


def _decode_s16hi(word: int) -> float:
    hi = (int(word) >> 16) & 0xFFFF
    signed = hi if hi < 0x8000 else hi - 0x10000
    return float(signed)


def _decode_s32(word: int) -> int:
    value = int(word) & 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def _safe_u32(bridge: EmulatorBridge, addr: int) -> int:
    try:
        return read_u32(bridge, addr)
    except Exception:
        return 0


def _safe_u8(bridge: EmulatorBridge, addr: int) -> int:
    try:
        return read_u8(bridge, addr)
    except Exception:
        return 0


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return default


def _coerce_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _read_contract_state(bridge: EmulatorBridge) -> dict | None:
    handler = getattr(bridge, "get_ram_features", None)
    if not callable(handler):
        return None

    try:
        payload = handler()
    except Exception:
        return None

    state_payload = payload.get("mk4_state_payload")
    if not isinstance(state_payload, dict):
        return None
    if state_payload.get("version") != MK4_STATE_CONTRACT_VERSION:
        return None
    if not bool(state_payload.get("available", False)):
        return None
    return state_payload


@dataclass
class FightState:
    """Snapshot of the current fight state read from the bridge."""

    frame_id: int = 0
    p1_health: int = HEALTH_MAX
    p2_health: int = HEALTH_MAX
    timer: int = 99
    p1_x: float = 0.0
    p2_x: float = 0.0

    # Combat signals kept for 14-float observation compatibility.
    p1_action: float = 0.0
    p2_action: float = 0.0
    p1_y_vel: float = 0.0
    p2_airborne: float = 0.0
    p1_hitstun: float = 0.0
    p2_hitstun: float = 0.0
    p1_airborne: float = 0.0

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


def _state_from_contract(contract: dict, frame_id: int) -> FightState:
    return FightState(
        frame_id=_coerce_int(contract.get("frame_id"), frame_id),
        p1_health=_coerce_int(contract.get("p1_health"), HEALTH_MAX),
        p2_health=_coerce_int(contract.get("p2_health"), HEALTH_MAX),
        timer=_coerce_int(contract.get("timer"), 99),
        p1_x=_coerce_float(contract.get("p1_x"), 0.0),
        p2_x=_coerce_float(contract.get("p2_x"), 0.0),
        p1_action=0.0,
        p2_action=0.0,
        p1_y_vel=_coerce_float(contract.get("p1_y_vel"), 0.0),
        p2_airborne=_coerce_float(contract.get("p2_airborne"), 0.0),
        p1_hitstun=0.0,
        p2_hitstun=0.0,
        p1_airborne=_coerce_float(contract.get("p1_airborne"), 0.0),
    )


def _state_from_direct_reads(bridge: EmulatorBridge, frame_id: int) -> FightState:
    p1_health = _decode_health_word(read_u32(bridge, P1_HEALTH_ADDR))
    p2_health = _decode_health_word(read_u32(bridge, P2_HEALTH_ADDR))
    timer = read_u8(bridge, FIGHT_TIMER_ADDR)
    p1_x = _decode_s16hi(read_u32(bridge, P1_X_ADDR))
    p2_x = _decode_s16hi(read_u32(bridge, P2_X_ADDR))

    p1_ground_word = _safe_u32(bridge, P1_GROUND_FLAG_ADDR)
    p2_air_word = _safe_u32(bridge, P2_AIR_FLAG_ADDR)
    p1_airborne = 1.0 if p1_ground_word == 1 else 0.0
    p2_airborne = 1.0 if (((p2_air_word >> 16) & 0xFFFF) != 0) else 0.0

    p1_y_vel_raw = _safe_u32(bridge, P1_Y_VEL_ADDR)
    p1_y_vel = max(-1.0, min(1.0, _decode_s32(p1_y_vel_raw) / Y_VEL_NORM))

    return FightState(
        frame_id=frame_id,
        p1_health=p1_health,
        p2_health=p2_health,
        timer=timer,
        p1_x=p1_x,
        p2_x=p2_x,
        p1_action=0.0,
        p2_action=0.0,
        p1_y_vel=p1_y_vel,
        p2_airborne=p2_airborne,
        p1_hitstun=0.0,
        p2_hitstun=0.0,
        p1_airborne=p1_airborne,
    )


def read_fight_state(bridge: EmulatorBridge, frame_id: int) -> FightState:
    """Read the current fight state from the bridge."""
    try:
        contract_state = _read_contract_state(bridge)
        if contract_state is not None:
            return _state_from_contract(contract_state, frame_id)
        return _state_from_direct_reads(bridge, frame_id)
    except Exception:
        return FightState(frame_id=frame_id)


def is_round_over(state: FightState) -> bool:
    """True when someone is KO'd or the timer runs out."""
    p1, p2, timer = state.p1_health, state.p2_health, state.timer

    if p1 == 0 and p2 == 0:
        return False
    if p1 == HEALTH_MAX and p2 == HEALTH_MAX:
        return False
    if (p1 == HEALTH_MAX and p2 == 0) or (p2 == HEALTH_MAX and p1 == 0):
        return False
    if p1 <= 0 or p2 <= 0:
        return True
    if timer == 0:
        return True
    return False


def p1_won(state: FightState) -> bool:
    """True when P1 wins the round (KO or timer expiry)."""
    p1, p2 = state.p1_health, state.p2_health
    if p1 == 0 and p2 == 0:
        return False
    if p2 <= 0 and p1 > 0:
        return True
    if p1 <= 0 and p2 > 0:
        return False
    if state.timer == 0:
        return p1 >= p2
    return False
