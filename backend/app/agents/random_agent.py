"""Random agent — picks weighted random macro actions."""

from __future__ import annotations

import random

from app.agents.base import FighterAgent
from app.services.actions import ActionPacket, MacroAction
from app.services.game_state import FightState


class RandomAgent(FighterAgent):
    """Simple random agent — picks a random macro action each step.

    Good for testing that the action loop and both controllers work.
    """

    _WEIGHTED_ACTIONS: list[tuple[MacroAction, int]] = [
        (MacroAction.NEUTRAL, 3),
        (MacroAction.ADVANCE, 5),
        (MacroAction.RETREAT, 3),
        (MacroAction.CROUCH, 2),
        (MacroAction.JUMP_FORWARD, 2),
        (MacroAction.JUMP_BACK, 1),
        (MacroAction.JUMP_NEUTRAL, 1),
        (MacroAction.RUN, 2),
        (MacroAction.STAND_BLOCK, 3),
        (MacroAction.CROUCH_BLOCK, 2),
        (MacroAction.LOW_PUNCH, 4),
        (MacroAction.HIGH_PUNCH, 4),
        (MacroAction.LOW_KICK, 3),
        (MacroAction.HIGH_KICK, 3),
        (MacroAction.JAB_COMBO, 2),
        (MacroAction.PUNISH, 1),
        (MacroAction.SPECIAL_1, 1),
        (MacroAction.THROW_ATTEMPT, 1),
    ]

    def __init__(self) -> None:
        self._actions: list[MacroAction] = []
        for action, weight in self._WEIGHTED_ACTIONS:
            self._actions.extend([action] * weight)
        self._hold_frames: int = 0
        self._current_action: MacroAction = MacroAction.NEUTRAL

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        if self._hold_frames <= 0:
            self._current_action = random.choice(self._actions)
            self._hold_frames = random.randint(3, 12)
        self._hold_frames -= 1

        return ActionPacket(
            macro_action=self._current_action,
            repeat_frames=1,
            player=player,
        )

    def reset(self) -> None:
        self._hold_frames = 0
        self._current_action = MacroAction.NEUTRAL
