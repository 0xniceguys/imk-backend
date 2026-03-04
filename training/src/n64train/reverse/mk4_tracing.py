"""
mk4_tracing.py — MK4 Fight-State Trace Provider
─────────────────────────────────────────────────
Reads P1/P2 health, fight timer, and character positions from RAM
via the debugger mem command and returns a TracedState.

Addresses are marked with their confidence level and can be updated
once find_fight_addrs.py confirms the exact values.

Usage:
    from n64train.reverse.mk4_tracing import Mk4FightTraceProvider
    provider = Mk4FightTraceProvider(helper)  # Mk4BridgeHelper
    state = provider.read(frame_id)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from n64train.runtime.types import TracedState


class DebugReaderLike(Protocol):
    def read_u8(self, virtual_address: int) -> int: ...
    def read_u32(self, virtual_address: int) -> int: ...


# ── Known fight-state addresses ───────────────────────────────────────────────
# These are PLACEHOLDER values until find_fight_addrs.py confirms them.
# Replace with confirmed values when scan is run.
#
# Address format: N64 virtual address (0x80xxxxxx)
#
# To verify an address manually:
#   h.read_u32(ADDR)  at round start (should be 0x10000 = 65536 for health, ~99 for timer)
#   after taking damage: h.read_u32(P1_HEALTH_ADDR) should be < 0x10000
#
# Placeholder until scan confirms real values.
# Set ADDRESSES_CONFIRMED = True once find_fight_addrs.py results are verified.

ADDRESSES_CONFIRMED = True

# Health: stored as u32 in 16.16 fixed-point format at the GameShark-documented
# addresses. Full health = 0x10000 (65536), dead = 0. Decreases per-hit in
# real-time (NOT the display-bar animation at 0x8036E729/2E which only updates
# at round end).
# Verified via frame-accurate pause/step/read with arcade savestate (2026-03-04):
#   P1: 65536 → 56361 → 45876 → 36701 → 27526 → 15730 → 6555 (monotonic decrease)
#   P2: 65536 → 55706 (one punch landed, then stable)
P1_HEALTH_ADDR   = 0x800FE0D8   # u32: internal health, 0x10000=full, 0=dead
P2_HEALTH_ADDR   = 0x80126F54   # u32: internal health, 0x10000=full, 0=dead

# Timer: counts down from 99
FIGHT_TIMER_ADDR = 0x80105118  # confirmed: reads 97 at round start (0x8010511B XOR3)

# Positions: stored as signed 16-bit value in the upper halfword of a 32-bit word.
# Read: (h.read_u32(ADDR) >> 16) interpreted as signed int16.
# P1: neutral=-2, right = +1/s, left = -1/s. Range roughly -10 to +10.
# P2: tracks CPU opponent walking independently of P1.
# Confirmed via live movement + idle-walk scan.
P1_X_ADDR = 0x800F87F8  # confirmed: hi-halfword; live test: -2→+9 tracking right
P2_X_ADDR = 0x8006A060  # confirmed: hi-halfword; CPU idle-walk: 1→2→3→4→5 independent of P1

# ── Addresses verified by live RAM differential scan (2026-03-03) ─────────────
#
# Method: loaded p1p2state.st (P1=Scorpion at round start), took idle baseline,
#   injected D_UP (jump) for 0.7s, mid-air snapshot, waited for landing, final
#   snapshot. Also ran A_BUTTON (punch) diff. Addresses below are confirmed by:
#   (a) changing on the relevant action, and/or
#   (b) PERFECT ARC — returning exactly to baseline after action ends.
#
# All addresses are in the P1 GameShark struct region (base 0x800FE000).
# P2 equivalent = base 0x80126E00, same offsets (needs separate verification run).
#
# ── CONFIRMED by live scan ────────────────────────────────────────────────────
#
# ACTION STATE (what move is P1 doing right now):
#   0x800FE08C  [GS_P1+0x08C]: idle=0, jump=4, punch(A)=4  (u32)
#   0x800FE0F8  [GS_P1+0x0F8]: idle=4, jump=1 → appears to be an "on-ground" flag
#
# Y MOTION / AIRBORNE:
#   0x800FE90C  [GS_P1+0x90C]: PERFECT ARC — 0x738→0xFFFFED1A→0x738 on jump
#                               s16hi: 0→-1→0 (negative upward velocity in N64 coords)
#   0x800FE924  [GS_P1+0x924]: PERFECT ARC — 0→0xFFFFE1E0→0 on jump (Y displacement?)
#
# HITBOX / PUNCH ACTIVE FRAMES:
#   0x800FE308  [GS_P1+0x308]: PERFECT ARC 0→0xB20→0 (non-zero only during punch)
#   0x800FE30C  [GS_P1+0x30C]: PERFECT ARC 0→0xFE2→0
#   0x800FE310  [GS_P1+0x310]: PERFECT ARC 0→0x84→0  ← smallest, clearest window
#
# ANIMATION LOG (rolling history of last 3 animation IDs):
#   0x800FE600  [GS_P1+0x600]: current anim ID (approx)
#   0x800FE604  [GS_P1+0x604]: previous anim ID
#   0x800FE608  [GS_P1+0x608]: one before that
#
# FACING / SIDE:
#   P1 facing: derived from P1_X vs P2_X (already done via 'facing_sign' in extras)
#   Needs dedicated facing scan (walk behind / crossover) for a direct address.
#
# CONFIRMED via GameShark database + verified values match:
#   0x800FE293  P1 character ID (u8)  = 0x00 = Scorpion ✅
#   0x80126E8F  P2 character ID (u8)  = 0x00 = Scorpion ✅
#   0x800F8506  P1 credits (u16)      = 3 ✅
#
# ── FLAG MEANINGS ─────────────────────────────────────────────────────────────
CANDIDATE_ADDRS_CONFIRMED = True   # ← live scan completed 2026-03-03

# Action state: 0 = idle/walking, 4 = jumping or attacking, check +0x0F8 for ground
P1_ACTION_STATE_ADDR = 0x800FE08C   # u32: 0=idle, 4=active
P1_GROUND_FLAG_ADDR  = 0x800FE0F8   # u32: 4=on_ground, 1=airborne

# Y velocity during jump (PERFECT ARC — returns to baseline on landing)
# Negative = going up (N64 world coords), positive = falling
P1_Y_VEL_ADDR        = 0x800FE90C   # u32: 0x738 idle, 0xFFFFED1A mid-jump

# Attack type register — DIFFERENT value for each attack (scanned 2026-03-03):
#   0      = idle / walking / blocking
#   69422  = LP (low punch, A)
#   67956  = HP (high punch, B)
#   68606  = HK (high kick, C-UP)
#   0      = LK (low kick, C-RIGHT) — same region as idle, needs deeper scan
# This lets the model distinguish attack types, not just "attacking or not"
P1_ATTACK_TYPE_ADDR = 0x800FE090   # u32: 0=idle, LP=69422, HP=67956, HK=68606
P1_LK_ADDR          = 0x800FE144   # u32: changes ONLY on LK (idle=-2146312416, LK=-2146312648)

# Hitstun / block flag — non-zero during HP active frames OR during block
# HP=292 (0x124), BLK=124 (0x7C), idle=0
P1_HITSTUN_ADDR      = 0x800FE310   # u32: 0 idle, non-zero during HP or block

# P2 equivalents — VERIFIED by P2 controller scan (2026-03-03)
# ⚠️  P2 struct has DIFFERENT offsets than P1!
#     P2 ground_flag  = +0x0CC  (P1 was +0x0F8)
#     P2 attack/punch = +0x094  (P1 was +0x310)
#     P2 anim pointer = +0x0C0  (PERFECT ARC on jump+punch)
#     P2 Y velocity   = NOT FOUND in P2 struct (different layout)
P2_BASE = 0x80126E00
P2_ACTION_STATE_ADDR = P2_BASE + 0x0C0   # 0x80126EC0 - anim pointer (PERFECT ARC)
P2_GROUND_FLAG_ADDR  = P2_BASE + 0x0CC   # 0x80126ECC - 2=ground, 1=air (PERFECT ARC)
P2_Y_VEL_ADDR        = 0x00000000        # NOT AVAILABLE — P2 struct lacks y_vel
P2_HITSTUN_ADDR      = P2_BASE + 0x19C   # 0x80126F9C - 0=idle, 2=attacking punch-only (NOT jump)

# P2 attack type registers — DIFFERENT value for each attack (BOTH scanned 2026-03-03):
#
# Primary: P2_ATTACK_TYPE_ADDR (+0x094) — high magnitude, covers LP/HP/HK:
#   0       = idle / LK / Block / Jump
#   -91881  = LP (low punch, A)
#   -87293  = HP (high punch, B)
#   -129236 = HK (high kick, C-UP)
#
# Secondary: P2_LK_ADDR (+0x130) — changes on ALL attacks inc. LK, distinct per type:
#   2946    = idle / neutral
#   3131    = LK (low kick, C-RIGHT)  ← unique!
#   3139    = LP (low punch, A)
#   3133    = HK (high kick, C-UP)
# Combined: primary=0 & secondary≠2946 → LK detected
P2_ATTACK_TYPE_ADDR = P2_BASE + 0x094   # 0x80126E94 - LP/HP/HK unique signed values
P2_LK_ADDR          = P2_BASE + 0x130   # 0x80126F30 - all attacks change this; LK=3131

# Character IDs (u32 word, LSB = char id)
P1_CHAR_WORD_ADDR = 0x800FE290   # u32: LSB = char (0x0B=Kai)
P2_CHAR_WORD_ADDR = 0x80126E8C   # u32: LSB = char (0x0A=Reptile)

HEALTH_RAW_MAX = 0x10000  # 65536 — full health in u32 16.16 fixed-point
HEALTH_MAX = 160          # normalized display scale (0-160), used by rewards/round logic
Y_VEL_NORM = 100000.0   # normalise Y velocity (typical range ~±0x10000)
ANIM_NORM  = 255.0      # action state IDs are small integers


@dataclass
class Mk4FightTraceProvider:
    """
    Reads fight state from RAM via the debugger bridge.

    When ADDRESSES_CONFIRMED is False returns a stub TracedState
    so training can dry-run without real addresses.
    When True, reads real values and returns a live TracedState.
    """
    helper: DebugReaderLike

    def _read_s16hi(self, addr: int) -> float | None:
        """Read the signed int16 in the upper halfword of a u32. Returns None on error."""
        try:
            w = self.helper.read_u32(addr)
            hi = (w >> 16) & 0xFFFF
            return float(hi if hi < 0x8000 else hi - 0x10000)
        except Exception:
            return None

    def _read_u8_safe(self, addr: int) -> int | None:
        """Read a u8 byte. Returns None on error."""
        try:
            return self.helper.read_u8(addr)
        except Exception:
            return None

    def _read_u32_safe(self, addr: int) -> int | None:
        try:
            return self.helper.read_u32(addr)
        except Exception:
            return None

    def read(self, frame_id: int) -> TracedState:
        if not ADDRESSES_CONFIRMED:
            # Return stub with full health so dry-run episodes work
            return TracedState(
                frame_id=frame_id,
                p1_health=HEALTH_MAX,
                p2_health=HEALTH_MAX,
                timer=99,
                p1_x=0.0,
                p2_x=100.0,
            )

        try:
            # Health: u32 read — 16.16 fixed-point, 0x10000 = full, 0 = dead
            # Normalize to 0-160 scale to keep reward magnitudes consistent
            p1_hp_raw = self.helper.read_u32(P1_HEALTH_ADDR)
            p2_hp_raw = self.helper.read_u32(P2_HEALTH_ADDR)
            p1_hp = int(p1_hp_raw * HEALTH_MAX / HEALTH_RAW_MAX)
            p2_hp = int(p2_hp_raw * HEALTH_MAX / HEALTH_RAW_MAX)
            timer  = self.helper.read_u8(FIGHT_TIMER_ADDR)
            # Positions are in the upper 16-bit halfword of the 32-bit word.
            # Extract and interpret as signed int16.
            p1_word = self.helper.read_u32(P1_X_ADDR)
            p2_word = self.helper.read_u32(P2_X_ADDR)
            p1_hi = (p1_word >> 16) & 0xFFFF
            p2_hi = (p2_word >> 16) & 0xFFFF
            p1_x  = float(p1_hi if p1_hi < 0x8000 else p1_hi - 0x10000)
            p2_x  = float(p2_hi if p2_hi < 0x8000 else p2_hi - 0x10000)
        except Exception:
            # Re-raise so the worker's retry loop (5x) can detect bridge failures.
            # Swallowing here meant workers streamed garbage rollouts after emulator crash.
            raise

        # ── Confirmed addresses (live scan 2026-03-03) ────────────────────────
        # All signals below are verified by jump/punch differential RAM scan.
        p1_action = 0.0
        p1_airborne = 0.0
        p1_y_vel = 0.0
        p1_hitstun = 0.0
        p2_action = 0.0
        p2_airborne = 0.0
        p2_y_vel = 0.0
        p2_hitstun = 0.0

        if CANDIDATE_ADDRS_CONFIRMED:
            # P1 attack type: 5-class normalized float encoding all attack types
            # 0.0=idle, 0.2=LK, 0.97=HP, 0.98=HK, 0.99=LP
            p1_atk_raw = self._read_u32_safe(P1_ATTACK_TYPE_ADDR)
            p1_lk_raw  = self._read_u32_safe(P1_LK_ADDR)
            # LK idle baseline = 0x80126B20 (u32). Changes to 0x80126A38 during LK.
            LK_IDLE_U32 = 0x80126B20
            # Priority: 0x090 register first (LP/HP/HK), then LK, else idle
            if p1_atk_raw and p1_atk_raw != 0:
                p1_action = min(1.0, float(p1_atk_raw) / 70000.0)
            elif p1_lk_raw is not None and p1_lk_raw != LK_IDLE_U32:
                p1_action = 0.2   # LK — distinct from all other attacks
            else:
                p1_action = 0.0   # idle / walking

            # P1 ground flag: 4=on_ground, 1=airborne
            p1_gnd_raw  = self._read_u32_safe(P1_GROUND_FLAG_ADDR)
            p1_airborne = float(p1_gnd_raw == 1) if p1_gnd_raw is not None else 0.0

            # P1 Y velocity (signed, PERFECT ARC during jump)
            # Read as signed 32-bit; normalize to [-1, +1] range
            p1_yv_raw = self._read_u32_safe(P1_Y_VEL_ADDR)
            if p1_yv_raw is not None:
                # Convert u32 → signed int32
                s32 = p1_yv_raw if p1_yv_raw < 0x80000000 else p1_yv_raw - 0x100000000
                p1_y_vel = max(-1.0, min(1.0, s32 / Y_VEL_NORM))
            else:
                p1_y_vel = 0.0

            # P1 hitstun: non-zero only while punch hitbox is active
            p1_hst_raw = self._read_u32_safe(P1_HITSTUN_ADDR)
            p1_hitstun = float(p1_hst_raw > 0) if p1_hst_raw is not None else 0.0

            # P2 attack type: 5-class via two registers (both scanned 2026-03-03)
            # Primary (+0x094): LP=-91881, HP=-87293, HK=-129236, idle/LK=0
            # Secondary (+0x130): idle=2946, LK=3131, LP=3139, HK=3133
            P2_LK_IDLE = 2946
            p2_atk_raw = self._read_u32_safe(P2_ATTACK_TYPE_ADDR)
            p2_lk_raw  = self._read_u32_safe(P2_LK_ADDR)
            if p2_atk_raw is not None and p2_atk_raw != 0:
                p2_action = min(1.0, abs(p2_atk_raw) / 130000.0)  # LP/HP/HK
            elif p2_lk_raw is not None and p2_lk_raw != P2_LK_IDLE:
                p2_action = 0.2   # LK — secondary register = 3131
            else:
                p2_action = 0.0   # idle / block / jump

            # P2 ground flag: 2=ground, 1=airborne (different from P1's 4/1!)
            p2_gnd_raw  = self._read_u32_safe(P2_GROUND_FLAG_ADDR)
            p2_airborne = float(p2_gnd_raw == 1) if p2_gnd_raw is not None else 0.0

            # P2 Y velocity: NOT AVAILABLE in P2 struct
            p2_y_vel = 0.0

            # P2 hitstun/attack: non-zero during punch/kick at +0x094
            p2_hst_raw = self._read_u32_safe(P2_HITSTUN_ADDR)
            p2_hitstun = float(p2_hst_raw != 0) if p2_hst_raw is not None else 0.0

        extras: dict = {
            # attack type: LP~0.71, HP~0.67, HK~0.99 for P1; LP~0.71, HP~0.67, HK~0.99 for P2; 0=idle
            'p1_action':   p1_action,
            'p2_action':   p2_action,
            # Airborne: 1.0 when in air, 0.0 on ground
            'p1_airborne': p1_airborne,
            'p2_airborne': p2_airborne,
            # Y velocity: negative = moving up, positive = falling. Clamped to [-1,+1]
            'p1_y_vel':    p1_y_vel,
            'p2_y_vel':    p2_y_vel,
            # Hitstun/block flag: non-zero during active attack frames or block stance
            'p1_hitstun':  p1_hitstun,
            'p2_hitstun':  p2_hitstun,
            # Crossover-aware facing
            'facing_sign': 1.0 if p2_x >= p1_x else -1.0,
        }

        return TracedState(
            frame_id  = frame_id,
            p1_health = p1_hp,
            p2_health = p2_hp,
            timer     = timer,
            p1_x      = p1_x,
            p2_x      = p2_x,
            p1_y      = p1_y_vel,    # repurpose p1_y field for y_vel
            p2_y      = p2_y_vel,
            p1_facing = int(p1_airborne),
            p2_facing = int(p2_airborne),
            extras    = extras,
        )

    def is_round_over(self, state: TracedState) -> bool:
        """True when someone's health reached zero or timer expired.

        Guards:
        - Both zero → uninitialized RAM (scene transition), not a real KO
        - Both at full (160) → fight hasn't started yet
        Note: we do NOT guard against P1=160/P2=0 because that's a legit
        perfect-round KO. The worker's settle + health-poll phase handles
        the initialization window before this is ever called.
        """
        p1 = state.p1_health
        p2 = state.p2_health
        timer = state.timer

        # Uninitialized: both zeroes means RAM not yet loaded
        if (p1 is None or p1 == 0) and (p2 is None or p2 == 0):
            return False

        # Fight hasn't started yet — both at full health
        if p1 == HEALTH_MAX and p2 == HEALTH_MAX:
            return False

        # KO — one player's health hit zero
        if p1 is not None and p1 <= 0:
            return True
        if p2 is not None and p2 <= 0:
            return True

        # Timer expired (only matters if timer isn't frozen)
        if timer is not None and timer == 0:
            return True

        return False

    def p1_won(self, state: TracedState) -> bool:
        """True when P1 wins the round."""
        p1 = state.p1_health
        p2 = state.p2_health
        # Guard against uninitialized zero reads
        if (p1 is None or p1 == 0) and (p2 is None or p2 == 0):
            return False
        if p2 is not None and p2 <= 0:
            return True
        # Timer-based win check — timer verified working (counts down, resets to 99)
        if (ADDRESSES_CONFIRMED and state.timer is not None and state.timer <= 0
                and p1 is not None and p2 is not None):
            return p1 > p2
        return False


def update_addresses(p1_health: int, p2_health: int, timer: int,
                     p1_x: int | None = None, p2_x: int | None = None,
                     round_state: int | None = None) -> None:
    """
    Call this after find_fight_addrs.py confirms the scan results.

    Example:
        from n64train.reverse.mk4_tracing import update_addresses
        update_addresses(p1_health=0x800F3A2C, p2_health=0x800F4A2C, timer=0x80048D40)
    """
    import n64train.reverse.mk4_tracing as _m
    _m.P1_HEALTH_ADDR  = p1_health
    _m.P2_HEALTH_ADDR  = p2_health
    _m.FIGHT_TIMER_ADDR = timer
    if p1_x is not None: _m.P1_X_ADDR = p1_x
    if p2_x is not None: _m.P2_X_ADDR = p2_x
    if round_state is not None: _m.ROUND_STATE_ADDR = round_state
    _m.ADDRESSES_CONFIRMED = True
