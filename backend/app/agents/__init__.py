"""
Agent registry — discovers available agents from folder structure and database.

Built-in agents (random, cpu) are always available.
Neural agents appear when:
  1. Their .onnx checkpoint exists in checkpoints/ (built-in)
  2. They exist in the Agent table (uploaded)

Usage:
    from app.agents import discover_agents, create_agent

    agents = discover_agents()          # list of AgentInfo
    agent = create_agent("mlp")         # instantiate by ID
"""

from __future__ import annotations

import asyncio
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


async def discover_agents_from_db() -> list[AgentInfo]:
    """Discover uploaded agents from database."""
    try:
        from sqlalchemy import select
        from app.db.engine import async_session
        from app.db.models import Agent

        async with async_session() as db:
            result = await db.execute(select(Agent).where(Agent.is_public == True))  # noqa: E712
            db_agents = result.scalars().all()

            return [
                AgentInfo(
                    id=f"custom_{agent.slug}",  # Prefix with "custom_" to avoid conflicts
                    name=agent.name,
                    description=agent.description or f"Uploaded {agent.architecture} agent",
                    has_checkpoint=True,
                    checkpoint_path=agent.checkpoint_path,
                    architecture=agent.architecture,
                )
                for agent in db_agents
            ]
    except Exception as e:
        logger.warning(f"Failed to load agents from DB: {e}")
        return []


def discover_agents() -> list[AgentInfo]:
    """Return all available agents. Neural agents only if checkpoint exists."""
    agents: list[AgentInfo] = []

    # Built-in agents from static definitions
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

    # Add uploaded agents from database
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Can't use asyncio.run() in running loop, skip DB agents
            logger.warning("Event loop already running, skipping DB agent discovery")
        else:
            db_agents = asyncio.run(discover_agents_from_db())
            agents.extend(db_agents)
    except RuntimeError:
        # No event loop, create one
        db_agents = asyncio.run(discover_agents_from_db())
        agents.extend(db_agents)

    return agents


def get_available_agents() -> list[AgentInfo]:
    """Return only agents that are ready to use (have checkpoints or are builtin)."""
    return [a for a in discover_agents() if a.has_checkpoint]


def create_agent(agent_id: str, checkpoint_path: str | None = None, architecture: str | None = None) -> FighterAgent:
    """Instantiate an agent by its ID.

    Args:
        agent_id: Agent identifier (e.g., "random", "lstm", "custom_my_agent")
        checkpoint_path: Optional path to checkpoint file (for custom agents)
        architecture: Neural network architecture (e.g., "lstm", "disc_rssm", "transformer")

    Raises ValueError if the agent ID is unknown or checkpoint is missing.
    """
    # Handle custom uploaded agents
    if agent_id.startswith("custom_"):
        if checkpoint_path is None:
            raise ValueError(f"Custom agent {agent_id!r} requires checkpoint_path")

        ckpt_path = Path(checkpoint_path)
        if not ckpt_path.exists():
            raise ValueError(f"Checkpoint not found for custom agent {agent_id!r}: {ckpt_path}")

        # ✅ FIX: Use architecture to instantiate correct agent class
        # Different architectures have different input/output shapes and state management
        if architecture == "lstm":
            from app.agents.onnx_agent import OnnxLstmAgent
            logger.info(f"Loading custom LSTM agent: {agent_id} from {ckpt_path}")
            return OnnxLstmAgent(ckpt_path)
        elif architecture == "disc_rssm":
            from app.agents.onnx_agent import OnnxDiscRssmAgent
            logger.info(f"Loading custom Discrete RSSM agent: {agent_id} from {ckpt_path}")
            return OnnxDiscRssmAgent(ckpt_path)
        elif architecture == "transformer":
            from app.agents.onnx_agent import OnnxTransformerAgent
            logger.info(f"Loading custom Transformer agent: {agent_id} from {ckpt_path}")
            return OnnxTransformerAgent(ckpt_path)
        elif architecture == "obj_belief" or architecture == "mlp" or architecture is None:
            # Generic single-input agents (or unknown - try generic)
            from app.agents.onnx_agent import OnnxAgent
            logger.info(f"Loading custom generic agent: {agent_id} ({architecture or 'unknown'}) from {ckpt_path}")
            return OnnxAgent(ckpt_path, use_frame_stack=True)
        else:
            # Unknown architecture - try generic and log warning
            from app.agents.onnx_agent import OnnxAgent
            logger.warning(f"Unknown architecture {architecture!r} for {agent_id}, using generic OnnxAgent")
            return OnnxAgent(ckpt_path, use_frame_stack=True)

    # Find definition in built-in agents
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
