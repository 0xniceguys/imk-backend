"""
Settlement service — settles a match in DB and resolves it on-chain.

Flow:
  1. Mark match COMPLETED in DB
  2. Update fighter stats
  3. Compute parimutuel payouts in DB
  4. Call lock_match + resolve_match on-chain via admin keypair
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

        # Determine winner fighter ID and on-chain side
        winner_id = match.fighter1_id if winner_player == 1 else match.fighter2_id
        winner_side = "A" if winner_player == 1 else "B"

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

        # Parimutuel payout (DB-side, mirrors contract math)
        active_bets = [b for b in match.bets if b.status == BetStatus.ACTIVE]
        total_pool  = sum(float(b.amount) for b in active_bets)
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
            match.current_round    = runner.current_round
            match.rounds_won_p1    = runner.rounds_won_p1
            match.rounds_won_p2    = runner.rounds_won_p2

        await db.commit()
        logger.info(
            "Match %s settled: winner=P%d, bets=%d, pool=%.4f",
            match_id, winner_player, len(active_bets), total_pool,
        )

    # ── On-chain: lock + resolve ──────────────────────────────────────────────
    if match.on_chain_match_pda:
        await _resolve_on_chain(match, winner_side)
    else:
        logger.info(
            "Match %s has no on_chain_match_pda — skipping on-chain resolve", match_id
        )


async def _resolve_on_chain(match: Match, winner_side: str) -> None:
    """Call lock_match then resolve_match on-chain using the admin keypair."""
    from app.config import settings
    from app.services import solana_tx
    from app.services.admin_keypair import get_admin_keypair

    if not settings.treasury_wallet:
        logger.error("TREASURY_WALLET not set — cannot resolve match on-chain")
        return

    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC

    try:
        admin_kp = get_admin_keypair()
    except ValueError as exc:
        logger.error("Admin keypair not configured: %s", exc)
        return

    try:
        # 1. lock_match
        blockhash = await solana_tx.get_recent_blockhash(rpc)
        lock_tx = solana_tx.build_lock_match_ix(
            admin_keypair=admin_kp,
            match_pda_str=match.on_chain_match_pda,
            blockhash=blockhash,
            program_id_str=settings.betting_program_id,
        )
        lock_sig = await solana_tx.send_transaction(lock_tx, rpc)
        logger.info("lock_match tx: %s (match_pda=%s)", lock_sig, match.on_chain_match_pda)

        # 2. resolve_match
        blockhash = await solana_tx.get_recent_blockhash(rpc)
        resolve_tx = solana_tx.build_resolve_match_ix(
            admin_keypair=admin_kp,
            match_pda_str=match.on_chain_match_pda,
            skr_mint_str=settings.skr_mint,
            treasury_wallet_str=settings.treasury_wallet,
            winner_side=winner_side,
            blockhash=blockhash,
            program_id_str=settings.betting_program_id,
        )
        resolve_sig = await solana_tx.send_transaction(resolve_tx, rpc)
        logger.info(
            "resolve_match tx: %s (match_pda=%s, winner=%s)",
            resolve_sig, match.on_chain_match_pda, winner_side,
        )

    except Exception as exc:
        # Log but don't crash — DB is already settled, on-chain failure can be retried
        logger.error(
            "On-chain settle failed for match %s (pda=%s): %s",
            match.id, match.on_chain_match_pda, exc,
            exc_info=True,
        )
