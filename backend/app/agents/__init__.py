"""
Agent registry — discovers available agents from folder structure.

Built-in agents (random, cpu) are always available.
Neural agents appear only when their .onnx checkpoint exists in checkpoints/.

Usage:
    from app.agents import discover_agents, create_agent

    agents = discover_agents()          # list of AgentInfo
    agent = create_agent("mlp")         # instantiate by ID
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.agents.base import AgentInfo, FighterAgent

logger = logging.getLogger(__name__)

CHECKPOINTS_DIR = Path(__file__).parent / "checkpoints"

# Agent definitions: id → metadata
# Neural agents reference a checkpoint filename — they only appear if the file exists.
_AGENT_DEFS: list[dict[str, Any]] = [
    {
        "id": "random",
        "name": "Random",
        "description": "Weighted random actions — good for testing",
        "architecture": "builtin",
        "needs_checkpoint": False,
    },
    {
        "id": "cpu",
        "name": "CPU (Neutral)",
        "description": "Sends no inputs — MK4 built-in AI controls",
        "architecture": "builtin",
        "needs_checkpoint": False,
    },
    {
        "id": "lstm",
        "name": "LSTM Policy",
        "description": "Trained LSTM with BPTT (recurrent memory)",
        "architecture": "lstm",
        "needs_checkpoint": True,
        "checkpoint": "lstm.onnx",
    },
    {
        "id": "obj_belief",
        "name": "Object Belief",
        "description": "Slot attention + belief head (auxiliary supervision)",
        "architecture": "obj_belief",
        "needs_checkpoint": True,
        "checkpoint": "obj_belief.onnx",
    },
    {
        "id": "disc_rssm",
        "name": "Discrete RSSM",
        "description": "GRU + discrete latent world model (hierarchical)",
        "architecture": "disc_rssm",
        "needs_checkpoint": True,
        "checkpoint": "disc_rssm.onnx",
    },
    {
        "id": "transformer",
        "name": "Transformer",
        "description": "Causal transformer with context window (hierarchical)",
        "architecture": "transformer",
        "needs_checkpoint": True,
        "checkpoint": "transformer.onnx",
    },
]


def discover_agents() -> list[AgentInfo]:
    """Return all available agents. Neural agents only if checkpoint exists."""
    agents: list[AgentInfo] = []
    for defn in _AGENT_DEFS:
        if defn["needs_checkpoint"]:
            ckpt_path = CHECKPOINTS_DIR / defn["checkpoint"]
            has_ckpt = ckpt_path.exists()
            agents.append(AgentInfo(
                id=defn["id"],
                name=defn["name"],
                description=defn["description"],
                has_checkpoint=has_ckpt,
                checkpoint_path=str(ckpt_path) if has_ckpt else None,
                architecture=defn["architecture"],
            ))
        else:
            agents.append(AgentInfo(
                id=defn["id"],
                name=defn["name"],
                description=defn["description"],
                has_checkpoint=True,  # builtins always available
                checkpoint_path=None,
                architecture=defn["architecture"],
            ))
    return agents


def get_available_agents() -> list[AgentInfo]:
    """Return only agents that are ready to use (have checkpoints or are builtin)."""
    return [a for a in discover_agents() if a.has_checkpoint]


def create_agent(agent_id: str) -> FighterAgent:
    """Instantiate an agent by its ID.

    Raises ValueError if the agent ID is unknown or checkpoint is missing.
    """
    # Find definition
    defn = None
    for d in _AGENT_DEFS:
        if d["id"] == agent_id:
            defn = d
            break
    if defn is None:
        raise ValueError(f"Unknown agent ID: {agent_id!r}")

    # Built-in agents
    if not defn["needs_checkpoint"]:
        if agent_id == "random":
            from app.agents.random_agent import RandomAgent
            return RandomAgent()
        elif agent_id == "cpu":
            from app.agents.cpu_agent import CPUAgent
            return CPUAgent()
        else:
            raise ValueError(f"No factory for builtin agent: {agent_id!r}")

    # Neural agents — need checkpoint
    ckpt_path = CHECKPOINTS_DIR / defn["checkpoint"]
    if not ckpt_path.exists():
        raise ValueError(
            f"Checkpoint not found for agent {agent_id!r}: {ckpt_path}"
        )

    arch = defn["architecture"]
    if arch == "obj_belief":
        from app.agents.onnx_agent import OnnxAgent
        return OnnxAgent(ckpt_path, use_frame_stack=True)
    elif arch == "lstm":
        from app.agents.onnx_agent import OnnxLstmAgent
        return OnnxLstmAgent(ckpt_path)
    elif arch == "disc_rssm":
        from app.agents.onnx_agent import OnnxDiscRssmAgent
        return OnnxDiscRssmAgent(ckpt_path)
    elif arch == "transformer":
        from app.agents.onnx_agent import OnnxTransformerAgent
        return OnnxTransformerAgent(ckpt_path)
    else:
        from app.agents.onnx_agent import OnnxAgent
        return OnnxAgent(ckpt_path, use_frame_stack=True)
