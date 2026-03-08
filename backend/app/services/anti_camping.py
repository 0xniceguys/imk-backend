"""
Anti-Camping Guard — detects stalled play and injects real N64 controller inputs.

Problem: trained agents sometimes camp (no position change for extended periods)
even while facing each other. This creates dull matches.

Solution: a thin rule layer that runs AFTER the agent brain but BEFORE write_ctrl.
When camping is detected, it overrides the next N steps with approach D-pad inputs
(D_RIGHT for P1, D_LEFT for P2) to close the gap.

Key design decisions:
  - Uses ONLY D-pad inputs (D_RIGHT / D_LEFT) — minimal interference with agent flow
  - Does NOT use macros or savestates — actual N64 button presses via ControllerState
  - Fires only when BOTH conditions are met: far apart AND not moving → no false triggers
  - Silent in normal play (no-op when characters are moving / at attack range)
  - Fully configurable thresholds via constants below

Coordinate system:
  P1_X, P2_X are signed i16 game-units from RAM (via _decode_s16hi).
  P1 starts on the LEFT (lower x), P2 on the RIGHT (higher x).
  At match start (from savestate), typical distance is ~80-120 units.
  At attack range it's ~20-40 units. At max corner distance ~200+ units.
"""
from __future__ import annotations

import logging
from collections import deque

from app.services.actions import Button, ControllerState

logger = logging.getLogger(__name__)

# ── Tunable constants ──────────────────────────────────────────────────────────

# How far apart the fighters must be (abs(p2_x - p1_x)) to consider camping.
# Below this distance they're already in striking range — no injection needed.
CAMP_DISTANCE_THRESHOLD: float = 80.0

# How little a player's x must change over the observation window to be flagged
# as camping. A player juking in place still moves a few units per step.
CAMP_MOVEMENT_THRESHOLD: float = 8.0

# Number of consecutive brain steps BOTH conditions must hold before injecting.
# At ~10 Hz brain rate: 10 steps = ~1 second of stuck play.
CAMP_IDLE_STEPS: int = 10

# How many brain steps to hold the approach input once camping is detected.
# Gives enough time to close some gap before handing back to the agent.
INJECT_STEPS: int = 8


class AntiCampingGuard:
    """
    Per-round guard — reset() should be called between rounds.

    Usage inside the agent brain loop (every step after reading game state):

        p1_ctrl_override, p2_ctrl_override = guard.check(state.p1_x, state.p2_x)

        # Override (if not None) replaces the agent's controller state:
        if p1_ctrl_override is not None:
            p1_controller = p1_ctrl_override
        else:
            p1_controller = resolve_action(p1_action).micro_controller_state

        # Same for P2 ...
    """

    def __init__(self) -> None:
        # Sliding window of recent x positions per player
        self._p1_hist: deque[float] = deque(maxlen=CAMP_IDLE_STEPS)
        self._p2_hist: deque[float] = deque(maxlen=CAMP_IDLE_STEPS)
        # Steps remaining in the current injection burst
        self._inject_remaining: int = 0
        # Tracks side assignment: True if P1 is on the left of P2 (normal)
        self._p1_on_left: bool = True

    # ── Public API ──────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Call at the start of each new round."""
        self._p1_hist.clear()
        self._p2_hist.clear()
        self._inject_remaining = 0

    def check(
        self,
        p1_x: float,
        p2_x: float,
    ) -> tuple[ControllerState | None, ControllerState | None]:
        """
        Returns (p1_override, p2_override).

        If no camping is detected, returns (None, None) and the agent's actions
        are used unmodified.  If camping is detected (or an injection burst is
        already in progress), returns D-pad approach states for both players.
        """
        self._p1_hist.append(p1_x)
        self._p2_hist.append(p2_x)

        # Determine which player is on which side this step
        self._p1_on_left = p1_x <= p2_x

        distance = abs(p2_x - p1_x)

        # ── Active injection burst ──────────────────────────────────────────
        if self._inject_remaining > 0:
            self._inject_remaining -= 1
            logger.debug(
                "[AntiCamp] injecting approach (%d steps left) dist=%.1f",
                self._inject_remaining, distance,
            )
            return self._approach_inputs()

        # ── Not enough history yet ──────────────────────────────────────────
        if len(self._p1_hist) < CAMP_IDLE_STEPS:
            return None, None

        # ── Already close — no intervention needed ──────────────────────────
        if distance < CAMP_DISTANCE_THRESHOLD:
            return None, None

        # ── Check if either player has been barely moving ───────────────────
        p1_range = max(self._p1_hist) - min(self._p1_hist)
        p2_range = max(self._p2_hist) - min(self._p2_hist)

        p1_stuck = p1_range < CAMP_MOVEMENT_THRESHOLD
        p2_stuck = p2_range < CAMP_MOVEMENT_THRESHOLD

        if p1_stuck or p2_stuck:
            logger.info(
                "[AntiCamp] 🏕️ Camping detected! dist=%.1f p1_move=%.1f p2_move=%.1f "
                "→ injecting %d approach steps",
                distance, p1_range, p2_range, INJECT_STEPS,
            )
            self._inject_remaining = INJECT_STEPS
            return self._approach_inputs()

        return None, None

    # ── Internal ────────────────────────────────────────────────────────────────

    def _approach_inputs(self) -> tuple[ControllerState, ControllerState]:
        """
        Returns D-pad inputs that move each player toward the other.

        MK4 N64 controller convention (from actions.py):
          P1 ADVANCE = D_RIGHT  (P1 starts on left, faces right)
          P2 ADVANCE = D_LEFT   (P2 starts on right, faces left)

        If the players' positions are reversed (unusual), mirror accordingly.
        """
        if self._p1_on_left:
            # Normal: P1 left → press D_RIGHT; P2 right → press D_LEFT
            p1_ctrl = ControllerState(pressed=frozenset({Button.D_RIGHT}))
            p2_ctrl = ControllerState(pressed=frozenset({Button.D_LEFT}))
        else:
            # Crossed: P1 has moved right of P2 — rare but handle it
            p1_ctrl = ControllerState(pressed=frozenset({Button.D_LEFT}))
            p2_ctrl = ControllerState(pressed=frozenset({Button.D_RIGHT}))
        return p1_ctrl, p2_ctrl
