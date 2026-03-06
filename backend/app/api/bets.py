"""bet API — all on-chain calls go through Privy server-side signing."""

from __future__ import annotations

import base64
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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


# ── Extended request body

class BetCreateOnChain(BaseModel):
    """Request body for POST /api/bets/"""
    match_id: str
    fighter_id: str
    amount: float          # in SKR float units (e.g. 1.5 SKR)
    side: str              # "A" (fighter1) or "B" (fighter2)
    privy_jwt: str | None = None  # optional only when dev_local_signer_bypass=true


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


async def _build_and_sign_place_bet(
    user: User,
    match_pda: str,
    side: str,
    amount_skr: float,
    privy_jwt: str | None,
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

    if not privy_jwt:
        raise HTTPException(400, "privy_jwt is required unless dev_local_signer_bypass=true")

    from app.services.privy_wallet import get_wallet_id_and_sign

    tx_b64 = base64.b64encode(tx_bytes).decode()
    sig, corrected_addr = await get_wallet_id_and_sign(
        user_jwt=privy_jwt,
        wallet_address=user.wallet_address,
        tx_b64=tx_b64,
        devnet=settings.use_devnet,
    )
    if corrected_addr:
        logger.warning("Wallet address mismatch: DB=%s Privy=%s", user.wallet_address, corrected_addr)

    confirmed = await solana_tx.confirm_transaction(sig, rpc, retries=settings.solana_confirm_retries)
    if not confirmed:
        raise HTTPException(502, f"On-chain bet transaction not confirmed: {sig}")

    return sig



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


@router.post("/", response_model=BetOut)
async def place_bet(
    body: BetCreateOnChain,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Place a bet on a match. Calls the Solana contract via Privy server-side signing."""
    from uuid import UUID as _UUID
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
        try:
            tx_sig = await _build_and_sign_place_bet(
                user=user,
                match_pda=match.on_chain_match_pda,
                side=body.side.upper(),
                amount_skr=body.amount,
                privy_jwt=body.privy_jwt,
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
