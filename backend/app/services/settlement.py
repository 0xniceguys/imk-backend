"""
Settlement service — settles a match in DB and resolves it on-chain.

Critical ordering rule:
1) If match is on-chain, lock/resolve (and required loser cleanup) must succeed first.
2) Only then persist winner/status/payout in DB.

This prevents frontend drift where DB reports a final winner while chain settlement failed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import Bet, BetStatus, Fighter, Match, MatchStatus, StreamStatus

logger = logging.getLogger(__name__)

BASE_UNITS = 1_000_000


class OnChainSettlementError(RuntimeError):
    """Raised when required on-chain settlement operations fail."""


def _to_base_units(amount: float | Decimal) -> int:
    dec = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return int((dec * Decimal(BASE_UNITS)).to_integral_value(rounding=ROUND_DOWN))


def _from_base_units(amount_base_units: int) -> Decimal:
    return (Decimal(amount_base_units) / Decimal(BASE_UNITS)).quantize(Decimal("0.000001"))


async def settle_match(match_id: str, winner_player: int) -> None:
    """Settle a match after it completes."""
    from app.db.engine import async_session

    if winner_player not in (1, 2):
        raise ValueError(f"winner_player must be 1 or 2, got {winner_player}")

    # Snapshot first, then perform on-chain actions, then commit DB finalization.
    async with async_session() as db:
        result = await db.execute(
            select(Match)
            .where(Match.id == UUID(match_id))
            .options(
                selectinload(Match.bets).selectinload(Bet.user),
                selectinload(Match.stream),
            )
        )
        match = result.scalar_one_or_none()
        if not match:
            logger.error("settle_match: match %s not found", match_id)
            return
        if match.status != MatchStatus.LIVE:
            logger.warning(
                "settle_match: match %s not LIVE (status=%s), skipping",
                match_id,
                match.status.value,
            )
            return

        winner_id = match.fighter1_id if winner_player == 1 else match.fighter2_id
        winner_side = "A" if winner_player == 1 else "B"
        if winner_id is None:
            raise RuntimeError(f"winner fighter ID is None for match {match_id}")

        active_bets = [b for b in match.bets if b.status == BetStatus.ACTIVE]
        winner_has_bets = any(b.fighter_id == winner_id for b in active_bets)
        losing_wallets = sorted(
            {
                b.user.wallet_address
                for b in active_bets
                if b.fighter_id != winner_id
                and b.on_chain_side in ("A", "B")
                and b.user is not None
                and b.user.wallet_address
            }
        )

        on_chain_match_pda = match.on_chain_match_pda

    fee_bps = int(settings.contract_fee_bps_default)

    if on_chain_match_pda:
        from app.services import solana_tx

        logger.info("Attempting on-chain settlement for match %s (PDA: %s)", match_id, on_chain_match_pda)
        rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
        try:
            cfg = await solana_tx.fetch_config(settings.betting_program_id, rpc)
        except Exception as exc:
            logger.error("Failed to fetch on-chain config for match %s: %s", match_id, exc)
            raise OnChainSettlementError(f"Failed to fetch on-chain config: {exc}") from exc
        fee_bps = int(cfg["fee_bps"])

        try:
            await _resolve_on_chain(
                match_id=match_id,
                match_pda=on_chain_match_pda,
                winner_side=winner_side,
                losing_wallets=losing_wallets,
                treasury_wallet=cfg["treasury_wallet"],
                config_admin_pubkey=cfg["admin"],
                require_full_loser_cleanup=not winner_has_bets,
            )
            logger.info("On-chain settlement successful for match %s", match_id)
        except Exception as exc:
            logger.error("On-chain settlement failed for match %s: %s", match_id, exc)
            raise
    else:
        logger.info(
            "Match %s has no on_chain_match_pda — proceeding with DB-only settlement",
            match_id,
        )

    # Finalize DB state only after on-chain success (or DB-only mode).
    async with async_session() as db:
        result = await db.execute(
            select(Match)
            .where(Match.id == UUID(match_id))
            .options(selectinload(Match.bets), selectinload(Match.stream))
        )
        match = result.scalar_one_or_none()
        if not match:
            logger.error("settle_match finalize: match %s not found", match_id)
            return
        if match.status != MatchStatus.LIVE:
            logger.warning(
                "settle_match finalize: match %s no longer LIVE (status=%s), skipping",
                match_id,
                match.status.value,
            )
            return

        winner_id = match.fighter1_id if winner_player == 1 else match.fighter2_id
        if winner_id is None:
            raise RuntimeError(f"winner fighter ID is None for match {match_id}")

        match.status = MatchStatus.COMPLETED
        match.winner_id = winner_id
        match.completed_at = datetime.now(timezone.utc)
        if match.stream:
            match.stream.status = StreamStatus.STOPPED

        for fid in (match.fighter1_id, match.fighter2_id):
            if fid is None:
                continue
            fighter_result = await db.execute(select(Fighter).where(Fighter.id == fid))
            fighter = fighter_result.scalar_one_or_none()
            if fighter is None:
                continue
            fighter.matches_played += 1
            if fid == winner_id:
                fighter.matches_won += 1

        active_bets = [b for b in match.bets if b.status == BetStatus.ACTIVE]
        total_pool_base = sum(_to_base_units(b.amount) for b in active_bets)
        winner_pool_base = sum(
            _to_base_units(b.amount) for b in active_bets if b.fighter_id == winner_id
        )
        fee_base = (total_pool_base * fee_bps) // 10_000
        payout_pool_base = total_pool_base - fee_base

        now = datetime.now(timezone.utc)
        for bet in active_bets:
            bet.settled_at = now
            if bet.fighter_id == winner_id and winner_pool_base > 0:
                bet_base = _to_base_units(bet.amount)
                payout_base = (payout_pool_base * bet_base) // winner_pool_base
                bet.status = BetStatus.WON
                bet.payout = _from_base_units(payout_base)
            else:
                bet.status = BetStatus.LOST
                bet.payout = Decimal("0")

        from app.services.match_runner import get_runner

        runner = get_runner(match_id)
        if runner:
            match.current_round = runner.current_round
            match.rounds_won_p1 = runner.rounds_won_p1
            match.rounds_won_p2 = runner.rounds_won_p2

        await db.commit()

        logger.info(
            "Match %s settled: winner=P%d, bets=%d, pool=%s, fee_bps=%d",
            match_id,
            winner_player,
            len(active_bets),
            str(_from_base_units(total_pool_base)),
            fee_bps,
        )


async def _resolve_on_chain(
    match_id: str,
    match_pda: str,
    winner_side: str,
    losing_wallets: list[str],
    treasury_wallet: str,
    config_admin_pubkey: str,
    require_full_loser_cleanup: bool,
) -> None:
    """Lock + resolve + (optional/required) loser cleanup on-chain."""
    from app.services import solana_tx
    from app.services.admin_keypair import get_admin_keypair

    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC

    try:
        admin_kp = get_admin_keypair()
    except ValueError as exc:
        raise OnChainSettlementError(f"Admin keypair not configured: {exc}") from exc

    match_state = await solana_tx.fetch_match(match_pda, rpc)
    if match_state is None:
        raise OnChainSettlementError(f"On-chain match account does not exist: {match_pda}")

    status = int(match_state["status"])
    if status == 3:
        raise OnChainSettlementError(f"On-chain match is cancelled: {match_pda}")
    if status == 0:
        blockhash = await solana_tx.get_recent_blockhash(rpc)
        lock_tx = solana_tx.build_lock_match_ix(
            admin_keypair=admin_kp,
            match_pda_str=match_pda,
            blockhash=blockhash,
            program_id_str=settings.betting_program_id,
        )
        lock_sig = await solana_tx.send_and_confirm_transaction(
            lock_tx, rpc, retries=settings.solana_confirm_retries
        )
        logger.info("lock_match tx: %s (match_pda=%s)", lock_sig, match_pda)
        status = 1
    elif status == 1:
        logger.info("match %s already LOCKED on-chain", match_pda)
    elif status == 2:
        logger.info("match %s already RESOLVED on-chain", match_pda)
    else:
        raise OnChainSettlementError(f"Unexpected on-chain match status={status} for {match_pda}")

    if status != 2:
        blockhash = await solana_tx.get_recent_blockhash(rpc)
        resolve_tx = solana_tx.build_resolve_match_ix(
            admin_keypair=admin_kp,
            match_pda_str=match_pda,
            skr_mint_str=settings.skr_mint,
            treasury_wallet_str=treasury_wallet,
            winner_side=winner_side,
            blockhash=blockhash,
            program_id_str=settings.betting_program_id,
        )
        resolve_sig = await solana_tx.send_and_confirm_transaction(
            resolve_tx, rpc, retries=settings.solana_confirm_retries
        )
        logger.info(
            "resolve_match tx: %s (match_pda=%s, winner=%s)",
            resolve_sig,
            match_pda,
            winner_side,
        )

    for wallet in losing_wallets:
        try:
            blockhash = await solana_tx.get_recent_blockhash(rpc)
            close_tx = solana_tx.build_close_losing_bet_ix(
                payer_keypair=admin_kp,
                match_pda_str=match_pda,
                losing_user_pubkey_str=wallet,
                admin_pubkey_str=config_admin_pubkey,
                blockhash=blockhash,
                program_id_str=settings.betting_program_id,
            )
            close_sig = await solana_tx.send_and_confirm_transaction(
                close_tx, rpc, retries=settings.solana_confirm_retries
            )
            logger.info(
                "close_losing_bet tx: %s (match=%s loser_wallet=%s)",
                close_sig,
                match_pda,
                wallet,
            )
        except Exception as exc:
            if require_full_loser_cleanup:
                raise OnChainSettlementError(
                    f"required loser cleanup failed for wallet {wallet}: {exc}"
                ) from exc
            logger.warning(
                "non-fatal loser cleanup failure for match %s wallet %s: %s",
                match_id,
                wallet,
                exc,
            )
