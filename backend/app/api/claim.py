"""
Claim endpoint — winners call this to claim their SKR payout on-chain.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from solders.transaction import Transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import get_current_user, get_db
from app.db.models import Bet, BetStatus, Match, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bets", tags=["bets"])
CLAIM_DISC = hashlib.sha256(b"global:claim").digest()[:8]


class ClaimRequest(BaseModel):
    privy_jwt: str | None = None  # optional only when dev_local_signer_bypass=true


class ClaimOut(BaseModel):
    bet_id: str
    tx_signature: str
    status: str  # "claimed"


class PrepareClaimResponse(BaseModel):
    transaction_base64: str
    message: str


class BroadcastClaimRequest(BaseModel):
    signed_transaction_base64: str


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


def _parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    return token or None


def _is_invalid_privy_jwt_http_error(exc: HTTPException) -> bool:
    if exc.status_code != 400:
        return False
    detail = str(exc.detail).lower()
    return (
        "invalid jwt" in detail
        or "invalid_data" in detail
        or "privy rejected" in detail
    )


async def _load_claimable_bet(
    *,
    bet_id: str,
    user: User,
    db: AsyncSession,
    allow_already_claimed: bool = False,
) -> Bet:
    try:
        bet_uuid = UUID(bet_id)
    except ValueError:
        raise HTTPException(422, "bet_id must be a valid UUID")

    result = await db.execute(
        select(Bet)
        .where(Bet.id == bet_uuid)
        .options(selectinload(Bet.match))
    )
    bet = result.scalar_one_or_none()
    if bet is None:
        raise HTTPException(404, "Bet not found")

    if bet.user_id != user.id:
        raise HTTPException(403, "Not your bet")

    if bet.status == BetStatus.CLAIMED:
        if allow_already_claimed:
            return bet
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

    return bet


async def _fetch_claim_build_inputs(*, rpc: str, program_id: str) -> tuple[str, str]:
    from app.services import solana_tx
    from app.services.admin_keypair import get_admin_keypair

    try:
        cfg = await solana_tx.fetch_config(program_id, rpc)
    except Exception as exc:
        raise HTTPException(502, f"Failed to fetch on-chain config: {exc}")

    treasury_wallet = cfg["treasury_wallet"]

    try:
        admin_kp = get_admin_keypair()
        admin_pubkey = str(admin_kp.pubkey())
    except ValueError as exc:
        raise HTTPException(500, f"Admin keypair not configured: {exc}")

    return treasury_wallet, admin_pubkey


async def _assert_on_chain_claimable(*, bet: Bet, rpc: str) -> None:
    from app.services import solana_tx

    match = bet.match
    if not match or not match.on_chain_match_pda:
        raise HTTPException(400, "This bet is not linked to an on-chain match — no claim possible")

    on_chain_match = await solana_tx.fetch_match(match.on_chain_match_pda, rpc)
    if on_chain_match is None:
        raise HTTPException(409, "On-chain match account does not exist")
    if on_chain_match["status"] != 2:
        raise HTTPException(409, "On-chain match is not resolved yet")

    expected_winner = 1 if bet.on_chain_side == "A" else 2
    if on_chain_match["winner"] != expected_winner:
        raise HTTPException(409, "On-chain winner does not match this bet side")


def _decode_claim_transaction(signed_transaction_base64: str) -> tuple[Transaction, str]:
    try:
        signed_tx_bytes = base64.b64decode(signed_transaction_base64, validate=True)
    except Exception as exc:
        raise HTTPException(400, "signed_transaction_base64 must be valid base64") from exc

    try:
        tx = Transaction.from_bytes(signed_tx_bytes)
    except Exception as exc:
        raise HTTPException(400, f"Invalid signed transaction bytes: {exc}") from exc

    msg = tx.message
    if len(msg.instructions) != 1:
        raise HTTPException(400, "Claim transaction must contain exactly one instruction")

    ix = msg.instructions[0]
    try:
        program_id = str(msg.account_keys[ix.program_id_index])
    except Exception as exc:
        raise HTTPException(400, f"Invalid instruction program index: {exc}") from exc
    if program_id != settings.betting_program_id:
        raise HTTPException(400, "Transaction targets the wrong on-chain program")

    data = bytes(ix.data)
    if len(data) != len(CLAIM_DISC) or data != CLAIM_DISC:
        raise HTTPException(400, "Transaction is not a valid claim instruction")

    return tx, str(msg.recent_blockhash)


def _assert_claim_message_matches_expected(
    *,
    tx: Transaction,
    user_wallet: str,
    match_pda: str,
    treasury_wallet: str,
    admin_pubkey: str,
    blockhash: str,
) -> None:
    from app.services import solana_tx

    if not tx.message.account_keys:
        raise HTTPException(400, "Transaction message has no account keys")
    if str(tx.message.account_keys[0]) != user_wallet:
        raise HTTPException(400, "Transaction fee payer does not match authenticated user wallet")

    expected_bytes = solana_tx.build_claim_ix(
        user_pubkey=user_wallet,
        match_pda_str=match_pda,
        skr_mint_str=settings.skr_mint,
        treasury_wallet_str=treasury_wallet,
        admin_pubkey_str=admin_pubkey,
        blockhash=blockhash,
        program_id_str=settings.betting_program_id,
    )
    expected_tx = Transaction.from_bytes(expected_bytes)
    if bytes(expected_tx.message) != bytes(tx.message):
        raise HTTPException(400, "Signed transaction content does not match expected claim parameters")


async def _broadcast_signed_transaction(
    *,
    signed_transaction_base64: str,
    rpc_url: str,
    fallback_sig: str | None = None,
) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    signed_transaction_base64,
                    {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"},
                ],
            },
        )

    if response.status_code != 200:
        raise HTTPException(502, f"Solana RPC error: {response.text}")

    payload = response.json()
    if "error" in payload:
        error_lower = str(payload["error"]).lower()
        if fallback_sig and ("already processed" in error_lower or "duplicate" in error_lower):
            return fallback_sig
        raise HTTPException(400, f"Transaction rejected: {payload['error']}")

    if "result" not in payload:
        raise HTTPException(502, f"Unexpected RPC response: {payload}")

    return str(payload["result"])


@router.post("/{bet_id}/claim/prepare", response_model=PrepareClaimResponse)
async def prepare_claim_payout(
    bet_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Prepare an unsigned claim transaction for client-side signing.

    Flutter signs this via Privy embedded wallet, then calls
    POST /bets/{bet_id}/claim/broadcast.
    """
    bet = await _load_claimable_bet(
        bet_id=bet_id,
        user=user,
        db=db,
        allow_already_claimed=False,
    )
    match = bet.match
    if not match or not match.on_chain_match_pda:
        raise HTTPException(400, "This bet is not linked to an on-chain match — no claim possible")

    from app.services import solana_tx

    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
    prog = settings.betting_program_id

    await _assert_on_chain_claimable(bet=bet, rpc=rpc)
    treasury_wallet, admin_pubkey = await _fetch_claim_build_inputs(rpc=rpc, program_id=prog)

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

    return PrepareClaimResponse(
        transaction_base64=base64.b64encode(tx_bytes).decode(),
        message=f"Sign transaction to claim payout for bet {bet_id}",
    )


@router.post("/{bet_id}/claim/broadcast", response_model=ClaimOut)
async def broadcast_claim_payout(
    bet_id: str,
    body: BroadcastClaimRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Broadcast a signed claim transaction and update DB after confirmation."""
    bet = await _load_claimable_bet(
        bet_id=bet_id,
        user=user,
        db=db,
        allow_already_claimed=True,
    )

    if bet.status == BetStatus.CLAIMED:
        if bet.claim_tx_signature:
            return ClaimOut(bet_id=bet_id, tx_signature=bet.claim_tx_signature, status="claimed")
        raise HTTPException(400, "Bet already claimed")

    match = bet.match
    if not match or not match.on_chain_match_pda:
        raise HTTPException(400, "This bet is not linked to an on-chain match — no claim possible")

    from app.services import solana_tx

    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
    prog = settings.betting_program_id

    await _assert_on_chain_claimable(bet=bet, rpc=rpc)
    treasury_wallet, admin_pubkey = await _fetch_claim_build_inputs(rpc=rpc, program_id=prog)

    tx, blockhash = _decode_claim_transaction(body.signed_transaction_base64)
    _assert_claim_message_matches_expected(
        tx=tx,
        user_wallet=user.wallet_address,
        match_pda=match.on_chain_match_pda,
        treasury_wallet=treasury_wallet,
        admin_pubkey=admin_pubkey,
        blockhash=blockhash,
    )

    tx_sig_from_payload = str(tx.signatures[0]) if tx.signatures else None
    if not tx_sig_from_payload:
        raise HTTPException(400, "Signed transaction is missing user signature")

    try:
        sig = await _broadcast_signed_transaction(
            signed_transaction_base64=body.signed_transaction_base64,
            rpc_url=rpc,
            fallback_sig=tx_sig_from_payload,
        )
    except HTTPException as exc:
        raise HTTPException(exc.status_code, _map_contract_error(Exception(str(exc.detail))))

    confirmed = await solana_tx.confirm_transaction(
        sig, rpc, retries=settings.solana_confirm_retries
    )
    if not confirmed:
        raise HTTPException(502, f"Claim transaction not confirmed: {sig}")

    bet.status = BetStatus.CLAIMED
    bet.claim_tx_signature = sig
    await db.commit()
    return ClaimOut(bet_id=bet_id, tx_signature=sig, status="claimed")


@router.post("/{bet_id}/claim", response_model=ClaimOut)
async def claim_payout(
    bet_id: str,
    body: ClaimRequest,
    authorization: str | None = Header(None),
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
        from app.services.privy_wallet import get_wallet_id_and_sign

        # Sign and broadcast via Privy
        tx_b64 = base64.b64encode(tx_bytes).decode()
        primary_jwt = body.privy_jwt.strip() if body.privy_jwt else None
        fallback_jwt = _parse_bearer_token(authorization)
        signer_jwt = primary_jwt or fallback_jwt
        if not signer_jwt:
            raise HTTPException(400, "privy_jwt is required unless a valid Bearer token is provided")
        try:
            sig, corrected_addr = await get_wallet_id_and_sign(
                user_jwt=signer_jwt,
                wallet_address=user.wallet_address,
                tx_b64=tx_b64,
                devnet=settings.use_devnet,
            )
        except HTTPException as exc:
            if (
                fallback_jwt
                and fallback_jwt != signer_jwt
                and _is_invalid_privy_jwt_http_error(exc)
            ):
                logger.warning(
                    "Privy signer rejected body privy_jwt during claim; retrying with Authorization bearer token",
                )
                sig, corrected_addr = await get_wallet_id_and_sign(
                    user_jwt=fallback_jwt,
                    wallet_address=user.wallet_address,
                    tx_b64=tx_b64,
                    devnet=settings.use_devnet,
                )
            else:
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
