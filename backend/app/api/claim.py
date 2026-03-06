"""
Claim endpoint — winners call this to claim their SKR payout on-chain.
"""

from __future__ import annotations

import base64
import logging
import re
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
    privy_jwt: str | None = None  # optional only when dev_local_signer_bypass=true


class ClaimOut(BaseModel):
    bet_id: str
    tx_signature: str
    status: str  # "claimed"


def _map_contract_error(exc: Exception) -> str:
    msg = str(exc)
    match = re.search(r"custom program error: 0x([0-9a-fA-F]+)", msg)
    if not match:
        return msg
    code = int(match.group(1), 16)
    mapping = {
        6004: "Match is not resolved on-chain yet.",
        6010: "This wallet is not a winner for the resolved side.",
        6012: "Winning total is zero for this match; claim is not possible.",
        6014: "Token mint mismatch in backend configuration.",
    }
    return mapping.get(code, msg)


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
    try:
        bet_uuid = UUID(bet_id)
    except ValueError:
        raise HTTPException(422, "bet_id must be a valid UUID")

    # Load bet with match relationship
    result = await db.execute(
        select(Bet)
        .where(Bet.id == bet_uuid)
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
    if not bet.on_chain_side:
        raise HTTPException(400, "This bet has no on-chain side mapping and cannot be claimed")

    match: Match = bet.match
    if not match or not match.on_chain_match_pda:
        raise HTTPException(400, "This bet is not linked to an on-chain match — no claim possible")

    if not user.wallet_address:
        raise HTTPException(400, "User has no Solana wallet linked")

    from app.services import solana_tx
    from app.services.admin_keypair import get_admin_keypair

    rpc  = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
    prog = settings.betting_program_id
    try:
        cfg = await solana_tx.fetch_config(prog, rpc)
    except Exception as exc:
        raise HTTPException(502, f"Failed to fetch on-chain config: {exc}")
    treasury_wallet = cfg["treasury_wallet"]

    # Guard against DB/on-chain drift before asking user to sign.
    on_chain_match = await solana_tx.fetch_match(match.on_chain_match_pda, rpc)
    if on_chain_match is None:
        raise HTTPException(409, "On-chain match account does not exist")
    if on_chain_match["status"] != 2:
        raise HTTPException(409, "On-chain match is not resolved yet")
    expected_winner = 1 if bet.on_chain_side == "A" else 2
    if on_chain_match["winner"] != expected_winner:
        raise HTTPException(409, "On-chain winner does not match this bet side")

    # Get admin pubkey for the treasury derivation (claim ix needs it)
    try:
        admin_kp = get_admin_keypair()
        admin_pubkey = str(admin_kp.pubkey())
    except ValueError as exc:
        raise HTTPException(500, f"Admin keypair not configured: {exc}")

    # Build the claim instruciton
    try:
        blockhash = await solana_tx.get_recent_blockhash(rpc)
        tx_bytes = solana_tx.build_claim_ix(
            user_pubkey=user.wallet_address,
            match_pda_str=match.on_chain_match_pda,
            skr_mint_str=settings.skr_mint,
            treasury_wallet_str=treasury_wallet,
            admin_pubkey_str=admin_pubkey,
            blockhash=blockhash,
            program_id_str=prog,
        )
    except Exception as exc:
        logger.error("Failed to build claim tx for bet %s: %s", bet_id, exc, exc_info=True)
        raise HTTPException(502, f"Failed to build claim transaction: {exc}")

    if settings.dev_local_signer_bypass:
        from app.services.dev_local_signer import sign_and_send_unsigned_tx_for_user

        try:
            sig = await sign_and_send_unsigned_tx_for_user(
                user=user,
                unsigned_tx_bytes=tx_bytes,
                rpc_url=rpc,
                retries=settings.solana_confirm_retries,
            )
        except Exception as exc:
            logger.error("Local signing failed for claim bet %s: %s", bet_id, exc, exc_info=True)
            raise HTTPException(502, f"Claim transaction signing failed: {_map_contract_error(exc)}")
    else:
        if not body.privy_jwt:
            raise HTTPException(400, "privy_jwt is required unless dev_local_signer_bypass=true")

        from app.services.privy_wallet import get_wallet_id_and_sign

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
            raise HTTPException(502, f"Claim transaction signing failed: {_map_contract_error(exc)}")

        confirmed = await solana_tx.confirm_transaction(sig, rpc, retries=settings.solana_confirm_retries)
        if not confirmed:
            raise HTTPException(502, f"Claim transaction not confirmed: {sig}")

    logger.info("Claim tx broadcast: bet=%s sig=%s user=%s", bet_id, sig, user.id)

    # Update DB
    bet.status = BetStatus.CLAIMED
    bet.claim_tx_signature = sig
    await db.commit()

    return ClaimOut(bet_id=bet_id, tx_signature=sig, status="claimed")
