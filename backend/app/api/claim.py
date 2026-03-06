"""
Claim endpoint — winners call this to claim their SKR payout on-chain.
"""

from __future__ import annotations

import base64
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import get_current_user, get_db
from app.db.models import Bet, BetStatus, Match, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bets", tags=["bets"])


class ClaimRequest(BaseModel):
    privy_jwt: str  # user's Privy access token for server-side signing


class ClaimOut(BaseModel):
    bet_id: str
    tx_signature: str
    status: str  # "claimed"


@router.post("/{bet_id}/claim", response_model=ClaimOut)
async def claim_payout(
    bet_id: str,
    body: ClaimRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Claim a won bet's SKR payout from the contract via Privy signing.

    The user must be authenticated and the bet must:
    - Belong to this user
    - Have status=WON
    - Have an on_chain_side set (was placed as an on-chain bet)
    """
    # Load bet with match relationship
    result = await db.execute(
        select(Bet)
        .where(Bet.id == UUID(bet_id))
        .options(selectinload(Bet.match))
    )
    bet = result.scalar_one_or_none()
    if bet is None:
        raise HTTPException(404, "Bet not found")

    # Auth check
    if bet.user_id != user.id:
        raise HTTPException(403, "Not your bet")

    # Status check
    if bet.status == BetStatus.CLAIMED:
        raise HTTPException(400, "Bet already claimed")
    if bet.status != BetStatus.WON:
        raise HTTPException(400, f"Bet status is '{bet.status.value}' — only WON bets can be claimed")

    match: Match = bet.match
    if not match or not match.on_chain_match_pda:
        raise HTTPException(400, "This bet is not linked to an on-chain match — no claim possible")

    if not user.wallet_address:
        raise HTTPException(400, "User has no Solana wallet linked")

    if not settings.treasury_wallet:
        raise HTTPException(500, "Treasury wallet is not configured (TREASURY_WALLET env var)")

    from app.services import solana_tx
    from app.services.privy_wallet import get_wallet_id_and_sign
    from app.services.admin_keypair import get_admin_keypair

    rpc  = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
    prog = settings.betting_program_id

    # Build the claim instruction (admin is gas sponsor)
    try:
        blockhash = await solana_tx.get_recent_blockhash(rpc)
        tx_bytes = solana_tx.build_claim_ix(
            user_pubkey=user.wallet_address,
            match_pda_str=match.on_chain_match_pda,
            skr_mint_str=settings.skr_mint,
            treasury_wallet_str=settings.treasury_wallet,
            admin_keypair=admin_kp,
            blockhash=blockhash,
            program_id_str=prog,
        )
    except Exception as exc:
        logger.error("Failed to build claim tx for bet %s: %s", bet_id, exc, exc_info=True)
        raise HTTPException(502, f"Failed to build claim transaction: {exc}")

    # Sign and broadcast via Privy
    tx_b64 = base64.b64encode(tx_bytes).decode()
    try:
        sig, corrected_addr = await get_wallet_id_and_sign(
            user_jwt=body.privy_jwt,
            wallet_address=user.wallet_address,
            tx_b64=tx_b64,
            devnet=settings.use_devnet,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Privy signing failed for claim bet %s: %s", bet_id, exc, exc_info=True)
        raise HTTPException(502, f"Claim transaction signing failed: {exc}")

    logger.info("Claim tx broadcast: bet=%s sig=%s user=%s", bet_id, sig, user.id)

    # Update DB
    bet.status = BetStatus.CLAIMED
    bet.claim_tx_signature = sig
    await db.commit()

    return ClaimOut(bet_id=bet_id, tx_signature=sig, status="claimed")
