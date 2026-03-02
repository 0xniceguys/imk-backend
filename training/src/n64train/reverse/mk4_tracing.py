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
#   h.read_u8(ADDR)  at round start (should be 0xA0 = 160 for health, ~99 for timer)
#   after taking damage: h.read_u8(P1_HEALTH_ADDR) should be < 0xA0
#
# Placeholder until scan confirms real values.
# Set ADDRESSES_CONFIRMED = True once find_fight_addrs.py results are verified.

ADDRESSES_CONFIRMED = True

# Note on addresses: scanner outputs  BASE + (dump_offset ^ 3).
# read_u8() applies ^3 internally, so we store (scanner_addr ^ 3) = BASE + dump_offset.
# Health: byte in [0x00, 0xA0] (0=dead, 160=full)
P1_HEALTH_ADDR   = 0x8036E729  # scan found 0x8036E72A → XOR3 → 0x8036E729
P2_HEALTH_ADDR   = 0x8036E72E  # scan found 0x8036E72D → XOR3 → 0x8036E72E

# Timer: counts down from 99
FIGHT_TIMER_ADDR = 0x80105118  # confirmed: reads 97 at round start (0x8010511B XOR3)

# Positions: stored as signed 16-bit value in the upper halfword of a 32-bit word.
# Read: (h.read_u32(ADDR) >> 16) interpreted as signed int16.
# P1: neutral=-2, right = +1/s, left = -1/s. Range roughly -10 to +10.
# P2: tracks CPU opponent walking independently of P1.
# Confirmed via live movement + idle-walk scan.
P1_X_ADDR = 0x800F87F8  # confirmed: hi-halfword; live test: -2→+9 tracking right
P2_X_ADDR = 0x8006A060  # confirmed: hi-halfword; CPU idle-walk: 1→2→3→4→5 independent of P1

# Round state: not yet confirmed
ROUND_STATE_ADDR = 0x00000000  # TODO

HEALTH_MAX = 0xA0  # 160 — full health


@dataclass
class Mk4FightTraceProvider:
    """
    Reads fight state from RAM via the debugger bridge.

    When ADDRESSES_CONFIRMED is False returns a stub TracedState
    so training can dry-run without real addresses.
    When True, reads real values and returns a live TracedState.
    """
    helper: DebugReaderLike

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
            p1_hp  = self.helper.read_u8(P1_HEALTH_ADDR)
            p2_hp  = self.helper.read_u8(P2_HEALTH_ADDR)
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

        return TracedState(
            frame_id  = frame_id,
            p1_health = p1_hp,
            p2_health = p2_hp,
            timer     = timer,
            p1_x      = p1_x,
            p2_x      = p2_x,
        )

    def is_round_over(self, state: TracedState) -> bool:
        """True when someone's health reached zero during an active fight.

        Guards against false triggers:
        - Both zero → uninitialized RAM (scene transition), not a real KO
        - Timer still at 99 → fight hasn't actually started yet
        - Either health at max (160) while the other is 0 → savestate just loaded,
          P2 RAM not yet populated; ignore.
        """
        p1 = state.p1_health
        p2 = state.p2_health
        timer = state.timer

        # Uninitialized: both zeroes means RAM not yet loaded
        if (p1 is None or p1 == 0) and (p2 is None or p2 == 0):
            return False

        # Fight hasn't started yet — check using health: if BOTH are at full (160),
        # the fight hasn't started. We no longer check timer >= 99 because we freeze
        # the timer at 99 every step to prevent CPU timeout wins.
        if p1 == HEALTH_MAX and p2 == HEALTH_MAX:
            return False

        # Timer == 0: round timed out — this IS a real round end.
        # (The fight-not-started guard above only fires when BOTH are at max health;
        # if one player has taken damage by timeout that guard won't trigger.)
        if timer is not None and timer == 0:
            return True

        # Sanity: at least one player must have started with health
        # If one is at full (160) and the other is 0, RAM is still initialising
        if (p1 == HEALTH_MAX and (p2 is None or p2 == 0)) or \
           (p2 == HEALTH_MAX and (p1 is None or p1 == 0)):
            return False

        if p1 is not None and p1 <= 0:
            return True
        if p2 is not None and p2 <= 0:
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
        if (ADDRESSES_CONFIRMED and state.timer is not None and state.timer <= 0
                and p1 is not None and p2 is not None):
            # Fix 4: strict > so equal-health timer-out is NOT labelled P1 win.
            # Equal health at timeout = draw in MK4; giving win_bonus here biases
            # the agent to think surviving with tied health = victory.
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
