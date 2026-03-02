"""
Base classes for fighter agents.

FighterAgent is the ABC that all agents implement.
AgentInfo describes an agent type for the UI/registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.services.actions import ActionPacket
from app.services.game_state import FightState


class FighterAgent(ABC):
    """Base class for fighter agents that generate actions each step."""

    @abstractmethod
    def choose_action(self, state: FightState, player: int) -> ActionPacket:
        """Given the current fight state, return an action for this player."""
        ...

    def reset(self) -> None:
        """Called at the start of a new round/match."""
        pass


@dataclass(frozen=True)
class AgentInfo:
    """Describes an available agent type for the UI."""
    id: str                     # "random", "mlp", "lstm", etc.
    name: str                   # Human-readable: "Random", "MLP Policy"
    description: str            # Short description
    has_checkpoint: bool        # True if checkpoint file exists (or builtin)
    checkpoint_path: str | None # Path to .pt file, None for builtins
    architecture: str           # "builtin", "mlp", "lstm"
