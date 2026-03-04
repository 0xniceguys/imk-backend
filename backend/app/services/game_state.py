"""
Game state reader — reads MK4 fight state from the emulator bridge.

Self-contained: no imports from the training package.
Prefers the bridge's canonical MK4 state contract when available and falls
back to direct debugger RAM reads for the verified core fight-state symbols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
TIMER_WORD_ADDR = FIGHT_TIMER_ADDR & ~0x3


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


@dataclass(frozen=True)
class _DebugRead:
    value: int | None
    error: str | None = None


def _debug_read_u8(bridge: EmulatorBridge, addr: int) -> _DebugRead:
    try:
        return _DebugRead(value=read_u8(bridge, addr))
    except Exception as exc:
        return _DebugRead(value=None, error=f"{type(exc).__name__}: {exc}")


def _debug_read_u32(bridge: EmulatorBridge, addr: int) -> _DebugRead:
    try:
        return _DebugRead(value=read_u32(bridge, addr))
    except Exception as exc:
        return _DebugRead(value=None, error=f"{type(exc).__name__}: {exc}")


def _serialize_debug_read(read: _DebugRead) -> dict[str, object]:
    return {
        "value": read.value,
        "error": read.error,
    }


def _read_direct_probe(bridge: EmulatorBridge) -> dict[str, object]:
    return {
        "timer_address": FIGHT_TIMER_ADDR,
        "timer_word_address": TIMER_WORD_ADDR,
        "timer_debugger_byte_address": FIGHT_TIMER_ADDR ^ 0x3,
        "p1_health_word": _debug_read_u32(bridge, P1_HEALTH_ADDR),
        "p2_health_word": _debug_read_u32(bridge, P2_HEALTH_ADDR),
        "timer_raw": _debug_read_u8(bridge, FIGHT_TIMER_ADDR),
        "timer_word_u32": _debug_read_u32(bridge, TIMER_WORD_ADDR),
        "p1_x_word": _debug_read_u32(bridge, P1_X_ADDR),
        "p2_x_word": _debug_read_u32(bridge, P2_X_ADDR),
        "p1_ground_flag_raw": _debug_read_u32(bridge, P1_GROUND_FLAG_ADDR),
        "p2_air_flag_word": _debug_read_u32(bridge, P2_AIR_FLAG_ADDR),
        "p1_y_vel_raw": _debug_read_u32(bridge, P1_Y_VEL_ADDR),
    }


def _serialize_direct_probe(probe: dict[str, object] | None) -> dict[str, object] | None:
    if probe is None:
        return None
    out: dict[str, object] = {}
    for key, value in probe.items():
        if isinstance(value, _DebugRead):
            out[key] = _serialize_debug_read(value)
        else:
            out[key] = value
    return out


def _probe_value(probe: dict[str, object] | None, key: str) -> int | None:
    if probe is None:
        return None
    value = probe.get(key)
    if not isinstance(value, _DebugRead):
        return None
    if value.value is None:
        return None
    return int(value.value)


def _require_probe_value(probe: dict[str, object], key: str) -> int:
    value = probe[key]
    if not isinstance(value, _DebugRead):
        raise TypeError(f"Probe value for {key!r} is not a debug read")
    if value.value is None:
        raise RuntimeError(f"Missing RAM value for {key}: {value.error or 'unknown error'}")
    return int(value.value)


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


def _contract_core_trusted(contract: dict[str, object] | None) -> bool:
    if not isinstance(contract, dict):
        return False
    p1_health = _coerce_int(contract.get("p1_health"), HEALTH_MAX)
    p2_health = _coerce_int(contract.get("p2_health"), HEALTH_MAX)
    timer = _coerce_int(contract.get("timer"), 99)

    if p1_health < 0 or p1_health > HEALTH_MAX:
        return False
    if p2_health < 0 or p2_health > HEALTH_MAX:
        return False
    if timer < 0 or timer > 99:
        return False
    if timer == 0 and p1_health > 0 and p2_health > 0:
        return False
    return True


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
    debug_info: dict[str, Any] = field(default_factory=dict)

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
        debug_info={
            "state_source": "contract",
            "contract_payload": dict(contract),
            "selected_frame_id": _coerce_int(contract.get("frame_id"), frame_id),
        },
    )


def _state_from_direct_reads(probe: dict[str, object], frame_id: int) -> FightState:
    p1_health_word = _require_probe_value(probe, "p1_health_word")
    p2_health_word = _require_probe_value(probe, "p2_health_word")
    timer_word = _require_probe_value(probe, "timer_word_u32")
    p1_x_word = _require_probe_value(probe, "p1_x_word")
    p2_x_word = _require_probe_value(probe, "p2_x_word")

    p1_health = _decode_health_word(p1_health_word)
    p2_health = _decode_health_word(p2_health_word)
    timer = timer_word & 0xFF
    p1_x = _decode_s16hi(p1_x_word)
    p2_x = _decode_s16hi(p2_x_word)

    p1_ground_word = _require_probe_value(probe, "p1_ground_flag_raw")
    p2_air_word = _require_probe_value(probe, "p2_air_flag_word")
    p1_airborne = 1.0 if p1_ground_word == 1 else 0.0
    p2_airborne = 1.0 if (((p2_air_word >> 16) & 0xFFFF) != 0) else 0.0

    p1_y_vel_raw = _require_probe_value(probe, "p1_y_vel_raw")
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
        debug_info={
            "state_source": "direct",
            "selected_frame_id": frame_id,
            "timer_decode_source": "timer_word_lsb",
            "direct_probe": _serialize_direct_probe(probe),
        },
    )


def _merge_state_sources(
    contract: dict[str, object] | None,
    probe: dict[str, object] | None,
    frame_id: int,
    previous_state: FightState | None,
) -> FightState:
    contract_trusted = _contract_core_trusted(contract)
    base_state = previous_state or FightState(frame_id=frame_id)
    source_map: dict[str, str] = {}

    def pick_int_field(
        field_name: str,
        *,
        direct_value: int | None = None,
        contract_key: str | None = None,
        default: int,
        previous_value: int | None = None,
    ) -> int:
        if direct_value is not None:
            source_map[field_name] = "direct"
            return int(direct_value)
        if previous_value is not None:
            source_map[field_name] = "previous"
            return int(previous_value)
        if contract_trusted and contract_key is not None and isinstance(contract, dict):
            source_map[field_name] = "contract"
            return _coerce_int(contract.get(contract_key), default)
        source_map[field_name] = "default"
        return int(default)

    def pick_float_field(
        field_name: str,
        *,
        direct_value: float | None = None,
        contract_key: str | None = None,
        default: float,
        previous_value: float | None = None,
    ) -> float:
        if direct_value is not None:
            source_map[field_name] = "direct"
            return float(direct_value)
        if previous_value is not None:
            source_map[field_name] = "previous"
            return float(previous_value)
        if contract_trusted and contract_key is not None and isinstance(contract, dict):
            source_map[field_name] = "contract"
            return _coerce_float(contract.get(contract_key), default)
        source_map[field_name] = "default"
        return float(default)

    p1_health_word = _probe_value(probe, "p1_health_word")
    p2_health_word = _probe_value(probe, "p2_health_word")
    timer_word = _probe_value(probe, "timer_word_u32")
    p1_x_word = _probe_value(probe, "p1_x_word")
    p2_x_word = _probe_value(probe, "p2_x_word")
    p1_ground_word = _probe_value(probe, "p1_ground_flag_raw")
    p2_air_word = _probe_value(probe, "p2_air_flag_word")
    p1_y_vel_raw = _probe_value(probe, "p1_y_vel_raw")

    p1_health = pick_int_field(
        "p1_health",
        direct_value=_decode_health_word(p1_health_word) if p1_health_word is not None else None,
        contract_key="p1_health",
        default=HEALTH_MAX,
        previous_value=previous_state.p1_health if previous_state is not None else None,
    )
    p2_health = pick_int_field(
        "p2_health",
        direct_value=_decode_health_word(p2_health_word) if p2_health_word is not None else None,
        contract_key="p2_health",
        default=HEALTH_MAX,
        previous_value=previous_state.p2_health if previous_state is not None else None,
    )
    timer = pick_int_field(
        "timer",
        direct_value=(timer_word & 0xFF) if timer_word is not None else None,
        contract_key="timer",
        default=99,
        previous_value=previous_state.timer if previous_state is not None else None,
    )
    p1_x = pick_float_field(
        "p1_x",
        direct_value=_decode_s16hi(p1_x_word) if p1_x_word is not None else None,
        contract_key="p1_x",
        default=0.0,
        previous_value=previous_state.p1_x if previous_state is not None else None,
    )
    p2_x = pick_float_field(
        "p2_x",
        direct_value=_decode_s16hi(p2_x_word) if p2_x_word is not None else None,
        contract_key="p2_x",
        default=0.0,
        previous_value=previous_state.p2_x if previous_state is not None else None,
    )
    p1_airborne = pick_float_field(
        "p1_airborne",
        direct_value=(1.0 if p1_ground_word == 1 else 0.0) if p1_ground_word is not None else None,
        contract_key="p1_airborne",
        default=0.0,
        previous_value=previous_state.p1_airborne if previous_state is not None else None,
    )
    p2_airborne = pick_float_field(
        "p2_airborne",
        direct_value=(1.0 if (((p2_air_word >> 16) & 0xFFFF) != 0) else 0.0) if p2_air_word is not None else None,
        contract_key="p2_airborne",
        default=0.0,
        previous_value=previous_state.p2_airborne if previous_state is not None else None,
    )
    p1_y_vel = pick_float_field(
        "p1_y_vel",
        direct_value=max(-1.0, min(1.0, _decode_s32(p1_y_vel_raw) / Y_VEL_NORM)) if p1_y_vel_raw is not None else None,
        contract_key="p1_y_vel",
        default=0.0,
        previous_value=previous_state.p1_y_vel if previous_state is not None else None,
    )

    if all(source == "direct" for source in source_map.values()):
        state_source = "direct"
    elif any(source == "direct" for source in source_map.values()):
        state_source = "merged"
    elif contract_trusted:
        state_source = "contract"
    else:
        state_source = "fallback"

    debug_info: dict[str, Any] = {
        "state_source": state_source,
        "selected_frame_id": frame_id,
        "timer_decode_source": "timer_word_lsb",
        "source_map": source_map,
        "contract_core_trusted": contract_trusted,
        "direct_probe": _serialize_direct_probe(probe),
        "contract_payload": dict(contract) if isinstance(contract, dict) else None,
    }

    if p1_health_word is not None:
        debug_info["p1_health_word_used"] = p1_health_word
    elif isinstance(contract, dict):
        debug_info["p1_health_word_used"] = contract.get("p1_health_word")
    if p2_health_word is not None:
        debug_info["p2_health_word_used"] = p2_health_word
    elif isinstance(contract, dict):
        debug_info["p2_health_word_used"] = contract.get("p2_health_word")

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
        debug_info=debug_info,
    )


def read_fight_state(
    bridge: EmulatorBridge,
    frame_id: int,
    previous_state: FightState | None = None,
) -> FightState:
    """Read the current fight state from the bridge."""
    contract_state: dict[str, object] | None = None
    direct_probe: dict[str, object] | None = None
    try:
        direct_probe = _read_direct_probe(bridge)
        contract_state = _read_contract_state(bridge)
        return _merge_state_sources(contract_state, direct_probe, frame_id, previous_state)
    except Exception as exc:
        return FightState(
            frame_id=frame_id,
            debug_info={
                "state_source": "fallback",
                "error": f"{type(exc).__name__}: {exc}",
                "contract_payload": dict(contract_state) if isinstance(contract_state, dict) else None,
                "direct_probe": _serialize_direct_probe(direct_probe),
            },
        )


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
