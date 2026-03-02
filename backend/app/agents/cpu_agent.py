"""CPU agent — sends no inputs, lets MK4 built-in CPU AI control."""

from __future__ import annotations

from app.agents.base import FighterAgent
from app.services.actions import ActionPacket, ControllerState, MacroAction
from app.services.game_state import FightState


class CPUAgent(FighterAgent):
    """Placeholder agent that sends no inputs — lets MK4 CPU AI control."""

    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        return ActionPacket(
            macro_action=MacroAction.NEUTRAL,
            micro_controller_state=ControllerState(),
            repeat_frames=1,
            player=player,
        )
