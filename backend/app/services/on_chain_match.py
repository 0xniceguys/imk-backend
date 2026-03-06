"""
On-chain match lifecycle service — the canonical layer for ALL Solana
contract interactions related to matches.

CRITICAL RULE: Every function is BLOCKING — the on-chain transaction must
be confirmed BEFORE the caller touches the DB.  No fire-and-forget.

Called by:
  - admin.py API endpoints
  - admin_views.py dashboard
  - match_runner.py (lock on start, auto-settle)
  - future automation scripts
"""

from __future__ import annotations

import hashlib
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def create_match_on_chain(
    fighter1_name: str,
    fighter2_name: str,
) -> tuple[int, str]:
    """
    Create a match on the Solana contract.  BLOCKING.

    Returns:
        (on_chain_match_id, on_chain_match_pda) — the caller should store
        these in the DB Match row.

    Raises:
        RuntimeError / ValueError on failure.
    """
    from solders.pubkey import Pubkey

    from app.services import solana_tx
    from app.services.admin_keypair import get_admin_keypair

    admin_kp = get_admin_keypair()  # raises ValueError if not set
    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC

    model_a_hash = hashlib.sha256(fighter1_name.encode()).digest()
    model_b_hash = hashlib.sha256(fighter2_name.encode()).digest()

    blockhash = await solana_tx.get_recent_blockhash(rpc)
    cfg = await solana_tx.fetch_config(settings.betting_program_id, rpc)
    match_counter = int(cfg["match_counter"])
    logger.info("On-chain match_counter before create: %d", match_counter)

    tx = solana_tx.build_create_match_ix(
        admin_keypair=admin_kp,
        skr_mint_str=settings.skr_mint,
        match_counter=match_counter,
        model_a_hash=model_a_hash,
        model_b_hash=model_b_hash,
        blockhash=blockhash,
        program_id_str=settings.betting_program_id,
    )
    sig = await solana_tx.send_and_confirm_transaction(
        tx, rpc, retries=settings.solana_confirm_retries
    )
    logger.info("create_match tx confirmed: %s (counter=%d)", sig, match_counter)

    # Verify the PDA actually exists on-chain
    prog_pk = Pubkey.from_string(settings.betting_program_id)
    match_pda = solana_tx.derive_match_pda(match_counter, prog_pk)
    match_pda_str = str(match_pda)
    if not await solana_tx.account_exists(match_pda_str, rpc):
        raise RuntimeError(f"create_match confirmed but PDA missing: {match_pda_str}")

    logger.info("On-chain match PDA verified: %s (id=%d)", match_pda_str, match_counter)
    return match_counter, match_pda_str


async def lock_match_on_chain(match_pda: str) -> str:
    """
    Lock a match on-chain (Open → Locked).  BLOCKING.

    Must be called BEFORE the match goes LIVE in DB or the emulator starts.
    After locking, place_bet will fail on-chain.

    Returns the tx signature.
    Raises RuntimeError on failure.
    """
    from app.services import solana_tx
    from app.services.admin_keypair import get_admin_keypair

    admin_kp = get_admin_keypair()
    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC

    # Check current on-chain status
    match_state = await solana_tx.fetch_match(match_pda, rpc)
    if match_state is None:
        raise RuntimeError(f"On-chain match account does not exist: {match_pda}")

    status = int(match_state["status"])
    if status == 1:
        logger.info("Match %s already Locked on-chain — skipping", match_pda)
        return "already_locked"
    if status != 0:
        raise RuntimeError(
            f"Cannot lock match {match_pda}: on-chain status={status} "
            f"(expected 0=Open)"
        )

    blockhash = await solana_tx.get_recent_blockhash(rpc)
    tx = solana_tx.build_lock_match_ix(
        admin_keypair=admin_kp,
        match_pda_str=match_pda,
        blockhash=blockhash,
        program_id_str=settings.betting_program_id,
    )
    sig = await solana_tx.send_and_confirm_transaction(
        tx, rpc, retries=settings.solana_confirm_retries
    )
    logger.info("lock_match tx confirmed: %s (match_pda=%s)", sig, match_pda)
    return sig


async def cancel_match_on_chain(match_pda: str) -> str:
    """
    Cancel a match on-chain (Open/Locked → Cancelled).  BLOCKING.

    Must be confirmed BEFORE the DB status changes.
    After cancellation, users can call refund_bet from the Flutter app.

    Returns the tx signature.
    Raises RuntimeError on failure.
    """
    from app.services import solana_tx
    from app.services.admin_keypair import get_admin_keypair

    admin_kp = get_admin_keypair()
    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC

    match_state = await solana_tx.fetch_match(match_pda, rpc)
    if match_state is None:
        raise RuntimeError(f"On-chain match account does not exist: {match_pda}")

    status = int(match_state["status"])
    if status == 3:
        logger.info("Match %s already Cancelled on-chain — skipping", match_pda)
        return "already_cancelled"
    if status == 2:
        raise RuntimeError(f"Cannot cancel resolved match on-chain: {match_pda}")
    if status not in (0, 1):
        raise RuntimeError(
            f"Unexpected on-chain match status={status} for {match_pda}"
        )

    blockhash = await solana_tx.get_recent_blockhash(rpc)
    tx = solana_tx.build_cancel_match_ix(
        admin_keypair=admin_kp,
        match_pda_str=match_pda,
        skr_mint_str=settings.skr_mint,
        blockhash=blockhash,
        program_id_str=settings.betting_program_id,
    )
    sig = await solana_tx.send_and_confirm_transaction(
        tx, rpc, retries=settings.solana_confirm_retries
    )
    logger.info("cancel_match tx confirmed: %s (match_pda=%s)", sig, match_pda)
    return sig
