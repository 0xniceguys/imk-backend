"""
Settlement service — settles a match, updates fighter stats, pays out bets.

Extracted from admin_views.py so it can be called both from admin UI
and from the match runner's auto-settle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Bet, BetStatus, Fighter, Match, MatchStatus, StreamStatus

logger = logging.getLogger(__name__)


async def settle_match(match_id: str, winner_player: int) -> None:
    """Settle a match after it completes.

    Args:
        match_id: The match UUID as a string.
        winner_player: 1 if P1 won, 2 if P2 won.
    """
    from app.db.engine import async_session

    async with async_session() as db:
        result = await db.execute(
            select(Match).where(Match.id == UUID(match_id))
            .options(selectinload(Match.bets), selectinload(Match.stream))
        )
        match = result.scalar_one_or_none()
        if not match:
            logger.error("settle_match: match %s not found", match_id)
            return

        # Determine winner fighter ID
        winner_id = match.fighter1_id if winner_player == 1 else match.fighter2_id
        if winner_id is None:
            logger.warning("settle_match: winner fighter ID is None for match %s", match_id)

        match.status = MatchStatus.COMPLETED
        match.winner_id = winner_id
        match.completed_at = datetime.now(timezone.utc)
        if match.stream:
            match.stream.status = StreamStatus.STOPPED

        # Update fighter stats
        for fid in (match.fighter1_id, match.fighter2_id):
            if fid is None:
                continue
            f_r = await db.execute(select(Fighter).where(Fighter.id == fid))
            fighter = f_r.scalar_one_or_none()
            if fighter is None:
                continue
            fighter.matches_played += 1
            if fid == winner_id:
                fighter.matches_won += 1

        # Parimutuel payout
        active_bets = [b for b in match.bets if b.status == BetStatus.ACTIVE]
        total_pool = sum(float(b.amount) for b in active_bets)
        winner_pool = sum(
            float(b.amount) for b in active_bets if b.fighter_id == winner_id
        )

        now = datetime.now(timezone.utc)
        for bet in active_bets:
            bet.settled_at = now
            if bet.fighter_id == winner_id:
                bet.status = BetStatus.WON
                bet.payout = (
                    round(float(bet.amount) * (total_pool / winner_pool), 6)
                    if winner_pool > 0
                    else float(bet.amount)
                )
            else:
                bet.status = BetStatus.LOST
                bet.payout = 0.0

        # Update round scores from runner if available
        from app.services.match_runner import get_runner
        runner = get_runner(match_id)
        if runner:
            match.current_round = runner.current_round
            match.rounds_won_p1 = runner.rounds_won_p1
            match.rounds_won_p2 = runner.rounds_won_p2

        await db.commit()
        logger.info(
            "Match %s settled: winner=P%d, bets=%d, pool=%.4f",
            match_id, winner_player, len(active_bets), total_pool,
        )
