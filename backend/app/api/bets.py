"""Bet API routes for both legacy server signing and client-signed flows."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import struct
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from uuid import UUID as _UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from solders.transaction import Transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import get_current_user, get_db
from app.db.models import Bet, BetStatus, Match, MatchStatus, User
from app.schemas.bet import BetOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bets", tags=["bets"])

MIN_BET = 0.01  # Minimum bet in SKR (float units)
SKR_DECIMALS = 1_000_000  # 1 SKR = 1,000,000 base units
PLACE_BET_DISC = hashlib.sha256(b"global:place_bet").digest()[:8]


# ── Extended request body

class BetCreateOnChain(BaseModel):
    """Request body for POST /api/bets/"""
    match_id: str
    fighter_id: str
    amount: float          # in SKR float units (e.g. 1.5 SKR)
    side: str              # "A" (fighter1) or "B" (fighter2)
    privy_jwt: str | None = None  # optional only when dev_local_signer_bypass=true


class BetPrepareRequest(BaseModel):
    match_id: str
    fighter_id: str
    amount: float
    side: str


class BetPrepareResponse(BaseModel):
    transaction_base64: str
    message: str


class BetBroadcastRequest(BaseModel):
    match_id: str
    signed_transaction_base64: str


# ── Helpers

def _bet_to_out(bet: Bet) -> BetOut:
    fighter_name  = bet.fighter.name  if bet.fighter  else ""
    opponent_name = ""
    if bet.match:
        if bet.fighter_id == bet.match.fighter1_id:
            opponent_name = bet.match.fighter2.name if bet.match.fighter2 else ""
        else:
            opponent_name = bet.match.fighter1.name if bet.match.fighter1 else ""

    return BetOut(
        id=bet.id,
        match_id=bet.match_id,
        fighter_id=bet.fighter_id,
        fighter_name=fighter_name,
        opponent_name=opponent_name,
        amount=float(bet.amount),
        currency=bet.currency,
        odds_at_placement=float(bet.odds_at_placement),
        status=bet.status.value if hasattr(bet.status, "value") else str(bet.status),
        payout=float(bet.payout) if bet.payout is not None else None,
        tx_signature=bet.tx_signature,
        on_chain_side=bet.on_chain_side,
        placed_at=bet.placed_at,
        settled_at=bet.settled_at,
    )


def _map_contract_error(exc: Exception) -> str:
    """
    Map common custom program errors to user-safe messages.
    Falls back to the raw exception text for unrecognized errors.
    """
    msg = str(exc)
    lower = msg.lower()
    if "already in use" in lower or "account in use" in lower:
        return "You already placed an on-chain bet for this match."

    match = re.search(r"custom program error: 0x([0-9a-fA-F]+)", msg)
    if not match:
        return msg

    code = int(match.group(1), 16)
    mapping = {
        6001: "Betting is paused on-chain right now.",
        6002: "This match is no longer open for betting on-chain.",
        6007: "Invalid side. Use side A for fighter1 or side B for fighter2.",
        6008: "Bet amount is outside on-chain min/max limits.",
        6009: "You already placed an on-chain bet for this match.",
        6014: "Token mint mismatch. Backend mint config is out of sync.",
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


async def _sign_with_privy_jwt_fallback(
    *,
    user_wallet_address: str,
    tx_b64: str,
    primary_jwt: str | None,
    fallback_bearer_jwt: str | None,
) -> tuple[str, str | None]:
    from app.services.privy_wallet import get_wallet_id_and_sign

    primary = primary_jwt.strip() if primary_jwt else None
    fallback = fallback_bearer_jwt.strip() if fallback_bearer_jwt else None
    signer_jwt = primary or fallback
    if not signer_jwt:
        raise HTTPException(400, "privy_jwt is required unless a valid Bearer token is provided")

    try:
        return await get_wallet_id_and_sign(
            user_jwt=signer_jwt,
            wallet_address=user_wallet_address,
            tx_b64=tx_b64,
            devnet=settings.use_devnet,
        )
    except HTTPException as exc:
        if (
            fallback
            and fallback != signer_jwt
            and _is_invalid_privy_jwt_http_error(exc)
        ):
            logger.warning(
                "Privy signer rejected body privy_jwt; retrying with Authorization bearer token",
            )
            return await get_wallet_id_and_sign(
                user_jwt=fallback,
                wallet_address=user_wallet_address,
                tx_b64=tx_b64,
                devnet=settings.use_devnet,
            )
        raise


async def _build_and_sign_place_bet(
    user: User,
    match_pda: str,
    side: str,
    amount_skr: float,
    privy_jwt: str | None,
    bearer_jwt: str | None,
) -> str:
    """Build place_bet tx, sign via Privy, return tx signature."""
    from app.services import solana_tx

    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
    mint = settings.skr_mint
    prog = settings.betting_program_id

    if not user.wallet_address:
        raise HTTPException(400, "User has no Solana wallet linked")

    blockhash = await solana_tx.get_recent_blockhash(rpc)
    amount_base = int(round(amount_skr * SKR_DECIMALS))

    tx_bytes = solana_tx.build_place_bet_ix(
        user_pubkey=user.wallet_address,
        match_pda_str=match_pda,
        skr_mint_str=mint,
        side=side,
        amount_base_units=amount_base,
        blockhash=blockhash,
        program_id_str=prog,
    )

    if settings.dev_local_signer_bypass:
        from app.services.dev_local_signer import sign_and_send_unsigned_tx_for_user

        return await sign_and_send_unsigned_tx_for_user(
            user=user,
            unsigned_tx_bytes=tx_bytes,
            rpc_url=rpc,
            retries=settings.solana_confirm_retries,
        )

    tx_b64 = base64.b64encode(tx_bytes).decode()
    sig, corrected_addr = await _sign_with_privy_jwt_fallback(
        user_wallet_address=user.wallet_address,
        tx_b64=tx_b64,
        primary_jwt=privy_jwt,
        fallback_bearer_jwt=bearer_jwt,
    )
    if corrected_addr:
        logger.warning("Wallet address mismatch: DB=%s Privy=%s", user.wallet_address, corrected_addr)

    confirmed = await solana_tx.confirm_transaction(sig, rpc, retries=settings.solana_confirm_retries)
    if not confirmed:
        raise HTTPException(502, f"On-chain bet transaction not confirmed: {sig}")

    return sig


def _amount_to_base_units(amount: float) -> int:
    try:
        scaled = (Decimal(str(amount)) * Decimal(SKR_DECIMALS)).to_integral_value(
            rounding=ROUND_DOWN
        )
        return int(scaled)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(400, "Invalid amount") from exc


def _amount_from_base_units(amount_base: int) -> float:
    amount = Decimal(amount_base) / Decimal(SKR_DECIMALS)
    return float(amount.quantize(Decimal("0.000001")))


async def _validate_amount_against_contract(match: Match, amount_base: int, amount_skr: float) -> None:
    # On-chain matches must obey on-chain config constraints exactly.
    if match.on_chain_match_pda:
        from app.services import solana_tx

        rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
        try:
            cfg = await solana_tx.fetch_config(settings.betting_program_id, rpc)
        except Exception as exc:
            raise HTTPException(502, f"Failed to fetch on-chain config: {exc}")

        if cfg["paused"]:
            raise HTTPException(400, "Betting is paused on-chain")
        if cfg["skr_mint"] != settings.skr_mint:
            raise HTTPException(
                500,
                f"Backend SKR mint mismatch with on-chain config: backend={settings.skr_mint} onchain={cfg['skr_mint']}",
            )
        if amount_base < int(cfg["min_bet"]) or amount_base > int(cfg["max_bet"]):
            raise HTTPException(
                400,
                f"Bet amount out of on-chain range [{cfg['min_bet']}, {cfg['max_bet']}] base units",
            )
    elif amount_skr < MIN_BET:
        raise HTTPException(400, f"Minimum bet is {MIN_BET} SKR")


async def _load_and_validate_bet_request(
    *,
    db: AsyncSession,
    match_id: str,
    fighter_id: str,
    amount: float,
    side: str,
    require_open_match: bool,
) -> tuple[Match, _UUID, str, int, float]:
    # Validate UUID formats up-front — asyncpg crashes on empty/invalid UUIDs
    try:
        match_uuid = _UUID(match_id)
        fighter_uuid = _UUID(fighter_id)
    except (ValueError, AttributeError):
        raise HTTPException(422, "match_id and fighter_id must be valid UUIDs")

    # Validate match
    result = await db.execute(
        select(Match)
        .where(Match.id == match_uuid)
        .options(
            selectinload(Match.bets),
            selectinload(Match.fighter1),
            selectinload(Match.fighter2),
        )
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    if require_open_match and match.status != MatchStatus.UPCOMING:
        raise HTTPException(400, "Betting closed — match is live or completed")

    # Validate fighter is in this match
    if fighter_uuid not in (match.fighter1_id, match.fighter2_id):
        raise HTTPException(400, "Fighter not in this match")

    # Validate amount
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    # Validate side
    side_upper = side.upper()
    if side_upper not in ("A", "B"):
        raise HTTPException(400, "side must be 'A' or 'B'")

    expected_side = "A" if fighter_uuid == match.fighter1_id else "B"
    if side_upper != expected_side:
        raise HTTPException(
            400,
            f"Invalid side for selected fighter. fighter_id maps to side '{expected_side}'.",
        )

    amount_base = _amount_to_base_units(amount)
    if amount_base <= 0:
        raise HTTPException(400, "Amount is too small after decimal conversion")

    await _validate_amount_against_contract(match, amount_base, amount)
    return match, fighter_uuid, side_upper, amount_base, amount


def _decode_place_bet_transaction(
    signed_transaction_base64: str,
) -> tuple[Transaction, str, int, str]:
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
        raise HTTPException(400, "Bet transaction must contain exactly one instruction")

    ix = msg.instructions[0]
    try:
        program_id = str(msg.account_keys[ix.program_id_index])
    except Exception as exc:
        raise HTTPException(400, f"Invalid instruction program index: {exc}") from exc

    if program_id != settings.betting_program_id:
        raise HTTPException(400, "Transaction targets the wrong on-chain program")

    data = bytes(ix.data)
    if len(data) != 17 or data[:8] != PLACE_BET_DISC:
        raise HTTPException(400, "Transaction is not a valid place_bet instruction")

    side_code = data[8]
    if side_code == 1:
        side = "A"
    elif side_code == 2:
        side = "B"
    else:
        raise HTTPException(400, "Transaction has invalid side encoding")

    amount_base = struct.unpack("<Q", data[9:17])[0]
    if amount_base <= 0:
        raise HTTPException(400, "Transaction amount must be positive")

    return tx, side, amount_base, str(msg.recent_blockhash)


def _assert_place_bet_message_matches_expected(
    *,
    tx: Transaction,
    user_wallet: str,
    match_pda: str,
    side: str,
    amount_base: int,
    blockhash: str,
) -> None:
    from app.services import solana_tx

    if not tx.message.account_keys:
        raise HTTPException(400, "Transaction message has no account keys")
    if str(tx.message.account_keys[0]) != user_wallet:
        raise HTTPException(400, "Transaction fee payer does not match authenticated user wallet")

    expected_bytes = solana_tx.build_place_bet_ix(
        user_pubkey=user_wallet,
        match_pda_str=match_pda,
        skr_mint_str=settings.skr_mint,
        side=side,
        amount_base_units=amount_base,
        blockhash=blockhash,
        program_id_str=settings.betting_program_id,
    )
    expected_tx = Transaction.from_bytes(expected_bytes)
    if bytes(expected_tx.message) != bytes(tx.message):
        raise HTTPException(400, "Signed transaction content does not match expected bet parameters")


def _snapshot_odds(match: Match, fighter_uuid: _UUID, amount_skr: float) -> float:
    active = [b for b in match.bets if b.status == BetStatus.ACTIVE]
    total = sum(float(b.amount) for b in active) + amount_skr
    fighter_pool = sum(float(b.amount) for b in active if b.fighter_id == fighter_uuid) + amount_skr
    return round(total / fighter_pool, 4) if fighter_pool > 0 else 2.0


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



# ── Routes

@router.get("/summary")
async def bet_summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated P&L stats for the current user."""
    result = await db.execute(
        select(Bet).where(
            Bet.user_id == user.id,
        )
    )
    bets = result.scalars().all()

    # Filter out cancelled bets from the P&L calculation
    settled = [
        b for b in bets
        if b.status in (BetStatus.ACTIVE, BetStatus.WON, BetStatus.LOST, BetStatus.CLAIMED)
    ]
    total_bets = len(settled)
    total_wagered = sum(float(b.amount) for b in settled)
    total_won = sum(
        float(b.payout) for b in settled
        if b.payout is not None and b.status in (BetStatus.WON, BetStatus.CLAIMED)
    )
    net_pnl = total_won - total_wagered
    won_count = sum(
        1 for b in settled if b.status in (BetStatus.WON, BetStatus.CLAIMED)
    )
    win_rate = round(won_count / total_bets, 4) if total_bets > 0 else 0.0

    return {
        "total_bets": total_bets,
        "total_wagered": round(total_wagered, 4),
        "total_won": round(total_won, 4),
        "net_pnl": round(net_pnl, 4),
        "win_rate": win_rate,
    }


@router.post("/prepare", response_model=BetPrepareResponse)
async def prepare_bet(
    body: BetPrepareRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Prepare an unsigned place_bet transaction for client-side signing.

    Flutter signs this via Privy embedded wallet, then calls POST /bets/broadcast.
    """
    if not user.wallet_address:
        raise HTTPException(400, "User has no Solana wallet linked")

    match, _fighter_uuid, side, amount_base, _amount_skr = await _load_and_validate_bet_request(
        db=db,
        match_id=body.match_id,
        fighter_id=body.fighter_id,
        amount=body.amount,
        side=body.side,
        require_open_match=True,
    )

    if not match.on_chain_match_pda:
        raise HTTPException(400, "Match is not linked to an on-chain match yet")

    from app.services import solana_tx

    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
    blockhash = await solana_tx.get_recent_blockhash(rpc)
    tx_bytes = solana_tx.build_place_bet_ix(
        user_pubkey=user.wallet_address,
        match_pda_str=match.on_chain_match_pda,
        skr_mint_str=settings.skr_mint,
        side=side,
        amount_base_units=amount_base,
        blockhash=blockhash,
        program_id_str=settings.betting_program_id,
    )

    return BetPrepareResponse(
        transaction_base64=base64.b64encode(tx_bytes).decode(),
        message=(
            f"Sign transaction to place {body.amount} SKR on side {side} "
            f"for match {body.match_id}"
        ),
    )


@router.post("/broadcast", response_model=BetOut)
async def broadcast_bet(
    body: BetBroadcastRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Broadcast a signed place_bet transaction and persist DB state only after
    on-chain confirmation.
    """
    if not user.wallet_address:
        raise HTTPException(400, "User has no Solana wallet linked")

    try:
        match_uuid = _UUID(body.match_id)
    except (ValueError, AttributeError):
        raise HTTPException(422, "match_id must be a valid UUID")

    result = await db.execute(
        select(Match)
        .where(Match.id == match_uuid)
        .options(
            selectinload(Match.bets),
            selectinload(Match.fighter1),
            selectinload(Match.fighter2),
        )
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    if not match.on_chain_match_pda:
        raise HTTPException(400, "Match is not linked to an on-chain match yet")

    tx, side, amount_base, blockhash = _decode_place_bet_transaction(
        body.signed_transaction_base64
    )
    _assert_place_bet_message_matches_expected(
        tx=tx,
        user_wallet=user.wallet_address,
        match_pda=match.on_chain_match_pda,
        side=side,
        amount_base=amount_base,
        blockhash=blockhash,
    )

    fighter_uuid = match.fighter1_id if side == "A" else match.fighter2_id
    if fighter_uuid is None:
        raise HTTPException(409, f"Match does not have a fighter mapped to side {side}")

    amount_skr = _amount_from_base_units(amount_base)
    await _validate_amount_against_contract(match, amount_base, amount_skr)

    tx_sig_from_payload = str(tx.signatures[0]) if tx.signatures else None
    if not tx_sig_from_payload:
        raise HTTPException(400, "Signed transaction is missing user signature")

    # Idempotency: if this exact transaction was already recorded, return it.
    existing_by_sig_result = await db.execute(
        select(Bet)
        .where(Bet.user_id == user.id, Bet.tx_signature == tx_sig_from_payload)
        .options(
            selectinload(Bet.fighter),
            selectinload(Bet.match).selectinload(Match.fighter1),
            selectinload(Bet.match).selectinload(Match.fighter2),
        )
    )
    existing_by_sig = existing_by_sig_result.scalar_one_or_none()
    if existing_by_sig:
        return _bet_to_out(existing_by_sig)

    # One on-chain bet per user per match.
    existing_on_match_result = await db.execute(
        select(Bet)
        .where(
            Bet.user_id == user.id,
            Bet.match_id == match.id,
            Bet.on_chain_side.is_not(None),
        )
        .order_by(Bet.placed_at.desc())
    )
    existing_on_match = existing_on_match_result.scalars().first()
    if existing_on_match and existing_on_match.tx_signature != tx_sig_from_payload:
        raise HTTPException(409, "You already placed an on-chain bet for this match")

    from app.services import solana_tx

    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
    try:
        tx_sig = await _broadcast_signed_transaction(
            signed_transaction_base64=body.signed_transaction_base64,
            rpc_url=rpc,
            fallback_sig=tx_sig_from_payload,
        )
    except HTTPException as exc:
        raise HTTPException(exc.status_code, _map_contract_error(Exception(str(exc.detail))))

    confirmed = await solana_tx.confirm_transaction(
        tx_sig, rpc, retries=settings.solana_confirm_retries
    )
    if not confirmed:
        raise HTTPException(502, f"On-chain bet transaction not confirmed: {tx_sig}")

    # Re-check idempotency after confirmation in case another worker wrote first.
    existing_after_confirm_result = await db.execute(
        select(Bet)
        .where(Bet.user_id == user.id, Bet.tx_signature == tx_sig)
        .options(
            selectinload(Bet.fighter),
            selectinload(Bet.match).selectinload(Match.fighter1),
            selectinload(Bet.match).selectinload(Match.fighter2),
        )
    )
    existing_after_confirm = existing_after_confirm_result.scalar_one_or_none()
    if existing_after_confirm:
        return _bet_to_out(existing_after_confirm)

    odds = _snapshot_odds(match, fighter_uuid, amount_skr)

    bet = Bet(
        user_id=user.id,
        match_id=match.id,
        fighter_id=fighter_uuid,
        amount=amount_skr,
        currency="SKR",
        odds_at_placement=odds,
        status=BetStatus.ACTIVE,
        tx_signature=tx_sig,
        on_chain_side=side,
    )
    db.add(bet)
    await db.commit()
    await db.refresh(bet)

    # Attach relationships for response
    bet.match = match
    bet.fighter = match.fighter1 if side == "A" else match.fighter2
    return _bet_to_out(bet)


@router.post("/", response_model=BetOut)
async def place_bet(
    body: BetCreateOnChain,
    authorization: str | None = Header(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Place a bet on a match. Calls the Solana contract via Privy server-side signing."""
    # Validate UUID formats up-front — asyncpg crashes on empty/invalid UUIDs
    try:
        match_uuid = _UUID(body.match_id)
        fighter_uuid = _UUID(body.fighter_id)
    except (ValueError, AttributeError):
        raise HTTPException(422, "match_id and fighter_id must be valid UUIDs")

    # Validate match
    result = await db.execute(
        select(Match)
        .where(Match.id == match_uuid)
        .options(
            selectinload(Match.bets),
            selectinload(Match.fighter1),
            selectinload(Match.fighter2),
        )
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    if match.status != MatchStatus.UPCOMING:
        raise HTTPException(400, "Betting closed — match is live or completed")

    # Validate fighter is in this match
    if fighter_uuid not in (match.fighter1_id, match.fighter2_id):
        raise HTTPException(400, "Fighter not in this match")

    # Validate amount
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    # Validate side
    if body.side.upper() not in ("A", "B"):
        raise HTTPException(400, "side must be 'A' or 'B'")

    expected_side = "A" if fighter_uuid == match.fighter1_id else "B"
    if body.side.upper() != expected_side:
        raise HTTPException(
            400,
            f"Invalid side for selected fighter. fighter_id maps to side '{expected_side}'.",
        )

    amount_base = int(round(body.amount * SKR_DECIMALS))
    if amount_base <= 0:
        raise HTTPException(400, "Amount is too small after decimal conversion")

    # On-chain matches must obey on-chain config constraints exactly.
    if match.on_chain_match_pda:
        from app.services import solana_tx

        rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
        try:
            cfg = await solana_tx.fetch_config(settings.betting_program_id, rpc)
        except Exception as exc:
            raise HTTPException(502, f"Failed to fetch on-chain config: {exc}")

        if cfg["paused"]:
            raise HTTPException(400, "Betting is paused on-chain")
        if cfg["skr_mint"] != settings.skr_mint:
            raise HTTPException(
                500,
                f"Backend SKR mint mismatch with on-chain config: backend={settings.skr_mint} onchain={cfg['skr_mint']}",
            )
        if amount_base < int(cfg["min_bet"]) or amount_base > int(cfg["max_bet"]):
            raise HTTPException(
                400,
                f"Bet amount out of on-chain range [{cfg['min_bet']}, {cfg['max_bet']}] base units",
            )
    elif body.amount < MIN_BET:
        raise HTTPException(400, f"Minimum bet is {MIN_BET} SKR")

    # Calculate current odds snapshot
    active = [b for b in match.bets if b.status == BetStatus.ACTIVE]
    total  = sum(float(b.amount) for b in active) + body.amount
    fighter_pool = (
        sum(float(b.amount) for b in active if b.fighter_id == fighter_uuid)
        + body.amount
    )
    odds = round(total / fighter_pool, 4) if fighter_pool > 0 else 2.0

    # ── On-chain place_bet ────────────────────────────────────────────────────
    tx_sig: str | None = None
    if match.on_chain_match_pda:
        bearer_jwt = _parse_bearer_token(authorization)
        try:
            tx_sig = await _build_and_sign_place_bet(
                user=user,
                match_pda=match.on_chain_match_pda,
                side=body.side.upper(),
                amount_skr=body.amount,
                privy_jwt=body.privy_jwt,
                bearer_jwt=bearer_jwt,
            )
            logger.info(
                "place_bet on-chain: match_pda=%s sig=%s user=%s",
                match.on_chain_match_pda, tx_sig, user.id,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("place_bet on-chain failed: %s", exc, exc_info=True)
            raise HTTPException(502, f"On-chain bet placement failed: {_map_contract_error(exc)}")
    else:
        # Match not yet created on-chain — warn and proceed DB-only
        logger.warning(
            "Match %s has no on_chain_match_pda — recording bet DB-only", match.id
        )

    # ── Persist to DB ─────────────────────────────────────────────────────────
    bet = Bet(
        user_id=user.id,
        match_id=match.id,
        fighter_id=fighter_uuid,
        amount=body.amount,
        currency="SKR",
        odds_at_placement=odds,
        tx_signature=tx_sig,
        on_chain_side=body.side.upper() if match.on_chain_match_pda else None,
    )
    db.add(bet)
    await db.commit()
    await db.refresh(bet)

    # Attach relationships for response
    bet.match = match
    bet.fighter = match.fighter1 if fighter_uuid == match.fighter1_id else match.fighter2

    return _bet_to_out(bet)


@router.get("/mine", response_model=list[BetOut])
async def my_bets(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bet)
        .where(Bet.user_id == user.id)
        .options(
            selectinload(Bet.fighter),
            selectinload(Bet.match).selectinload(Match.fighter1),
            selectinload(Bet.match).selectinload(Match.fighter2),
        )
        .order_by(Bet.placed_at.desc())
    )
    bets = result.scalars().all()
    return [_bet_to_out(b) for b in bets]


class RefundRequest(BaseModel):
    privy_jwt: str | None = None


@router.post("/{bet_id}/refund")
async def refund_bet(
    bet_id: str,
    body: RefundRequest,
    authorization: str | None = Header(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Refund a user's bet from a cancelled on-chain match.

    The user must sign the refund_bet transaction (via Privy)
    to reclaim their SKR from the contract vault.
    """
    import uuid
    try:
        bet_uuid = uuid.UUID(bet_id)
    except ValueError:
        raise HTTPException(422, "bet_id must be a valid UUID")

    result = await db.execute(
        select(Bet)
        .where(Bet.id == bet_uuid, Bet.user_id == user.id)
        .options(selectinload(Bet.match))
    )
    bet = result.scalar_one_or_none()
    if not bet:
        raise HTTPException(404, "Bet not found or does not belong to you")

    if bet.status != BetStatus.CANCELLED:
        raise HTTPException(400, f"Bet status is {bet.status.value}, must be CANCELLED to refund")

    match = bet.match
    if not match:
        raise HTTPException(400, "Associated match not found")
    if match.status != MatchStatus.CANCELLED:
        raise HTTPException(400, "Match is not cancelled")
    if not match.on_chain_match_pda:
        raise HTTPException(400, "Match was not on-chain — no on-chain refund needed")
    if not bet.on_chain_side:
        raise HTTPException(400, "Bet was not placed on-chain — no on-chain refund needed")

    if not user.wallet_address:
        raise HTTPException(400, "User has no Solana wallet linked")

    from app.services import solana_tx

    rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC

    # Get admin pubkey from on-chain config
    try:
        cfg = await solana_tx.fetch_config(settings.betting_program_id, rpc)
        admin_pubkey = cfg["admin"]
    except Exception as exc:
        raise HTTPException(502, f"Failed to fetch on-chain config: {exc}")

    blockhash = await solana_tx.get_recent_blockhash(rpc)
    tx_bytes = solana_tx.build_refund_bet_ix(
        user_pubkey=user.wallet_address,
        match_pda_str=match.on_chain_match_pda,
        skr_mint_str=settings.skr_mint,
        admin_pubkey_str=admin_pubkey,
        blockhash=blockhash,
        program_id_str=settings.betting_program_id,
    )

    # Sign via dev signer or Privy
    if settings.dev_local_signer_bypass:
        from app.services.dev_local_signer import sign_and_send_unsigned_tx_for_user

        sig = await sign_and_send_unsigned_tx_for_user(
            user=user,
            unsigned_tx_bytes=tx_bytes,
            rpc_url=rpc,
            retries=settings.solana_confirm_retries,
        )
    else:
        tx_b64 = base64.b64encode(tx_bytes).decode()
        sig, _ = await _sign_with_privy_jwt_fallback(
            user_wallet_address=user.wallet_address,
            tx_b64=tx_b64,
            primary_jwt=body.privy_jwt,
            fallback_bearer_jwt=_parse_bearer_token(authorization),
        )
        confirmed = await solana_tx.confirm_transaction(
            sig, rpc, retries=settings.solana_confirm_retries
        )
        if not confirmed:
            raise HTTPException(502, f"Refund transaction not confirmed: {sig}")

    # Update bet record
    bet.tx_signature = sig
    bet.status = BetStatus.CANCELLED  # stays CANCELLED but now has tx_sig
    await db.commit()

    logger.info(
        "Bet %s refunded on-chain: tx=%s match=%s user=%s",
        bet_id, sig, match.id, user.id,
    )
    return {"status": "refunded", "bet_id": bet_id, "tx_signature": sig}
