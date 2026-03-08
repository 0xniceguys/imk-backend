"""Shared match cancellation flow (contract-first).

This module centralizes cancellation behavior so all paths (admin API, admin UI,
runner failures, startup failures) keep on-chain and DB state consistent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import BetStatus, Match, MatchStatus, StreamStatus

logger = logging.getLogger(__name__)


@dataclass
class MatchCancelResult:
    match_id: str
    on_chain_tx: str | None
    cancelled_bets: int


async def cancel_match_contract_first(
    db: AsyncSession,
    match: Match,
    *,
    stream_status: StreamStatus = StreamStatus.STOPPED,
    reason: str = "unspecified",
) -> MatchCancelResult:
    """Cancel a match with contract-first ordering.

    Order:
    1) If on-chain match exists, call cancel_match and wait for confirmation.
    2) Only then mark DB match/bets as cancelled.
    """
    if match.status == MatchStatus.COMPLETED:
        raise RuntimeError(f"Cannot cancel completed match: {match.id}")

    on_chain_tx: str | None = None
    if match.on_chain_match_pda:
        from app.services.on_chain_match import cancel_match_on_chain

        on_chain_tx = await cancel_match_on_chain(match.on_chain_match_pda)

    now = datetime.now(timezone.utc)
    match.status = MatchStatus.CANCELLED
    if match.completed_at is None:
        match.completed_at = now

    if match.stream:
        match.stream.status = stream_status

    cancelled_bets = 0
    for bet in match.bets:
        if bet.status == BetStatus.ACTIVE:
            bet.status = BetStatus.CANCELLED
            bet.settled_at = now
            cancelled_bets += 1

    await db.commit()
    logger.info(
        "Match %s cancelled (%s): on_chain_tx=%s active_bets_cancelled=%d",
        match.id,
        reason,
        on_chain_tx,
        cancelled_bets,
    )
    return MatchCancelResult(
        match_id=str(match.id),
        on_chain_tx=on_chain_tx,
        cancelled_bets=cancelled_bets,
    )


async def cancel_match_by_id_contract_first(
    match_id: str | uuid.UUID,
    *,
    stream_status: StreamStatus = StreamStatus.STOPPED,
    reason: str = "unspecified",
) -> MatchCancelResult | None:
    """Load and cancel a match using a fresh DB session."""
    from app.db.engine import async_session

    match_uuid = match_id if isinstance(match_id, uuid.UUID) else uuid.UUID(str(match_id))

    async with async_session() as db:
        result = await db.execute(
            select(Match)
            .where(Match.id == match_uuid)
            .options(selectinload(Match.bets), selectinload(Match.stream))
        )
        match = result.scalar_one_or_none()
        if not match:
            logger.warning("cancel_match_by_id: match %s not found", match_uuid)
            return None
        return await cancel_match_contract_first(
            db,
            match,
            stream_status=stream_status,
            reason=reason,
        )

