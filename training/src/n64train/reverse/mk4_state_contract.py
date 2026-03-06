from __future__ import annotations

from typing import Any

from n64train.runtime.types import TracedState


MK4_STATE_CONTRACT_VERSION = "mk4_core_v1"
HEALTH_FP_ONE = 0x00010000
HEALTH_MAX = 160


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def decode_health_word(word: int) -> int:
    return int(round(_clamp01(float(word) / float(HEALTH_FP_ONE)) * HEALTH_MAX))


def decode_s16hi(word: int) -> int:
    hi = (int(word) >> 16) & 0xFFFF
    return hi if hi < 0x8000 else hi - 0x10000


def decode_s32(word: int) -> int:
    value = int(word) & 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def mk4_state_contract_payload() -> dict[str, Any]:
    from n64train.reverse.mk4_tracing import (
        FIGHT_TIMER_ADDR,
        P1_GROUND_FLAG_ADDR,
        P1_HEALTH_ADDR,
        P1_X_ADDR,
        P1_Y_VEL_ADDR,
        P2_GROUND_FLAG_ADDR,
        P2_HEALTH_ADDR,
        P2_X_ADDR,
    )

    return {
        "version": MK4_STATE_CONTRACT_VERSION,
        "transport": {
            "address_space": "n64_virtual_rdram",
            "u32_reads": "read the logical address exactly as written",
            "u8_reads": "read the logical address exactly as written through the helper/client wrapper",
            "raw_debugger_u8_reads": "use logical_address ^ 0x3 when issuing raw debugger mem /1b commands",
            "sampling": "set inputs, step deterministically, then read RAM immediately from the stepped frame",
        },
        "symbols": {
            "p1_health_word": {
                "address": P1_HEALTH_ADDR,
                "address_hex": f"0x{P1_HEALTH_ADDR:08X}",
                "width_bytes": 4,
                "read_kind": "u32",
                "decode": "round(clamp(word / 0x00010000, 0.0, 1.0) * 160)",
                "notes": "Canonical internal P1 health word. Full health = 0x00010000.",
            },
            "p2_health_word": {
                "address": P2_HEALTH_ADDR,
                "address_hex": f"0x{P2_HEALTH_ADDR:08X}",
                "width_bytes": 4,
                "read_kind": "u32",
                "decode": "round(clamp(word / 0x00010000, 0.0, 1.0) * 160)",
                "notes": "Canonical internal P2 health word. Full health = 0x00010000.",
            },
            "timer": {
                "address": FIGHT_TIMER_ADDR,
                "address_hex": f"0x{FIGHT_TIMER_ADDR:08X}",
                "width_bytes": 1,
                "read_kind": "u8",
                "decode": "direct byte value",
                "debugger_byte_address_hex": f"0x{(FIGHT_TIMER_ADDR ^ 0x3):08X}",
            },
            "p1_x": {
                "address": P1_X_ADDR,
                "address_hex": f"0x{P1_X_ADDR:08X}",
                "width_bytes": 4,
                "read_kind": "u32",
                "decode": "signed upper 16 bits",
            },
            "p2_x": {
                "address": P2_X_ADDR,
                "address_hex": f"0x{P2_X_ADDR:08X}",
                "width_bytes": 4,
                "read_kind": "u32",
                "decode": "signed upper 16 bits",
            },
            "p1_airborne": {
                "address": P1_GROUND_FLAG_ADDR,
                "address_hex": f"0x{P1_GROUND_FLAG_ADDR:08X}",
                "width_bytes": 4,
                "read_kind": "u32",
                "decode": "1.0 iff word == 1 else 0.0",
            },
            "p2_airborne": {
                "address": P2_GROUND_FLAG_ADDR,
                "address_hex": f"0x{P2_GROUND_FLAG_ADDR:08X}",
                "width_bytes": 4,
                "read_kind": "u32",
                "decode": "1.0 iff upper 16 bits are non-zero else 0.0",
            },
            "p1_y_vel_raw": {
                "address": P1_Y_VEL_ADDR,
                "address_hex": f"0x{P1_Y_VEL_ADDR:08X}",
                "width_bytes": 4,
                "read_kind": "u32",
                "decode": "signed 32-bit vertical velocity word",
            },
        },
    }


def mk4_state_payload(traced_state: TracedState | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": MK4_STATE_CONTRACT_VERSION,
        "available": traced_state is not None,
    }
    if traced_state is None:
        return payload

    extras = traced_state.extras or {}
    p1_health_word = extras.get("p1_health_word")
    p2_health_word = extras.get("p2_health_word")
    p1_y_vel_raw = extras.get("p1_y_vel_raw")

    payload.update(
        {
            "frame_id": traced_state.frame_id,
            "p1_health_word": p1_health_word,
            "p2_health_word": p2_health_word,
            "p1_health": traced_state.p1_health,
            "p2_health": traced_state.p2_health,
            "timer": traced_state.timer,
            "timer_raw": extras.get("timer_raw"),
            "p1_x": traced_state.p1_x,
            "p2_x": traced_state.p2_x,
            "p1_airborne": extras.get("p1_airborne"),
            "p2_airborne": extras.get("p2_airborne"),
            "p1_ground_flag_raw": extras.get("p1_ground_flag_raw"),
            "p2_air_flag_word": extras.get("p2_air_flag_word"),
            "p1_y_vel": extras.get("p1_y_vel"),
            "p1_y_vel_raw": p1_y_vel_raw,
            "p1_facing": traced_state.p1_facing,
            "p2_facing": traced_state.p2_facing,
            "facing_sign": extras.get("facing_sign"),
        }
    )
    return payload
