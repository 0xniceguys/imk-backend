"""Helpers for resolving fighter-linked agent and savestate config."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Fighter, FighterMatchupSavestate

VALID_BUILTIN_AGENT_IDS = {
    "random",
    "cpu",
    "lstm",
    "obj_belief",
    "disc_rssm",
    "transformer",
}


def resolve_agent_runtime(
    fighter: Fighter | None,
    *,
    fallback_agent_id: str = "random",
) -> tuple[str, str | None, str | None]:
    """Return (agent_id, checkpoint_path, architecture) for runtime loading."""
    if fighter is None:
        return fallback_agent_id, None, None

    if fighter.agent_id and fighter.agent:
        return (
            f"custom_{fighter.agent.slug}",
            fighter.agent.checkpoint_path,
            fighter.agent.architecture,
        )

    if fighter.agent_architecture in VALID_BUILTIN_AGENT_IDS:
        return fighter.agent_architecture, None, None

    return fallback_agent_id, None, None


async def get_fighter_with_agent(db: AsyncSession, fighter_id: UUID) -> Fighter | None:
    """Fetch one fighter with attached custom agent loaded."""
    result = await db.execute(
        select(Fighter)
        .where(Fighter.id == fighter_id)
        .options(selectinload(Fighter.agent))
    )
    return result.scalar_one_or_none()


async def resolve_matchup_savestate_path(
    db: AsyncSession,
    *,
    fighter1_id: UUID,
    fighter2_id: UUID,
) -> str | None:
    """Resolve savestate path for ordered matchup (fighter1 as P1, fighter2 as P2)."""
    result = await db.execute(
        select(FighterMatchupSavestate)
        .where(
            FighterMatchupSavestate.left_fighter_id == fighter1_id,
            FighterMatchupSavestate.right_fighter_id == fighter2_id,
            FighterMatchupSavestate.is_active.is_(True),
        )
        .order_by(
            FighterMatchupSavestate.is_default.desc(),
            FighterMatchupSavestate.priority.desc(),
            FighterMatchupSavestate.created_at.desc(),
        )
    )
    mapping = result.scalars().first()
    return mapping.savestate_path if mapping else None
