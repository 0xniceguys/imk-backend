"""bet API — all on-chain calls go through Privy server-side signing."""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import get_current_user, get_db
from app.db.models import Bet, BetStatus, Match, MatchStatus, User
from app.schemas.bet import BetCreate, BetOut

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
    privy_jwt: str         # user's Privy access token for server-side signing


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
        placed_at=bet.placed_at,
        settled_at=bet.settled_at,
    )


async def _build_and_sign_place_bet(
    user: User,
    match_pda: str,
    side: str,
    amount_skr: float,
    privy_jwt: str,
) -> str:
    """Build place_bet tx (admin-sponsored gas), sign via Privy, return tx sig."""
    from app.services import solana_tx
    from app.services.privy_wallet import get_wallet_id_and_sign
    from app.services.admin_keypair import get_admin_keypair

    rpc  = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
    mint = settings.skr_mint
    prog = settings.betting_program_id

    if not user.wallet_address:
        raise HTTPException(400, "User has no Solana wallet linked")

    try:
        admin_kp = get_admin_keypair()
    except ValueError as exc:
        raise HTTPException(500, f"Admin keypair not configured (needed for gas sponsorship): {exc}")

    blockhash    = await solana_tx.get_recent_blockhash(rpc)
    amount_base  = int(round(amount_skr * SKR_DECIMALS))

    tx_bytes = solana_tx.build_place_bet_ix(
        user_pubkey=user.wallet_address,
        match_pda_str=match_pda,
        skr_mint_str=mint,
        side=side,
        amount_base_units=amount_base,
        blockhash=blockhash,
        admin_keypair=admin_kp,
        program_id_str=prog,
    )

    tx_b64 = base64.b64encode(tx_bytes).decode()
    sig, corrected_addr = await get_wallet_id_and_sign(
        user_jwt=privy_jwt,
        wallet_address=user.wallet_address,
        tx_b64=tx_b64,
        devnet=settings.use_devnet,
    )
    # If Privy returned a different address than DB, log but don't fail
    if corrected_addr:
        logger.warning("Wallet address mismatch: DB=%s Privy=%s", user.wallet_address, corrected_addr)

    return sig


# ── Routes

@router.post("/", response_model=BetOut)
async def place_bet(
    body: BetCreateOnChain,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Place a bet on a match. Calls the Solana contract via Privy server-side signing."""
    # Validate match
    result = await db.execute(
        select(Match)
        .where(Match.id == body.match_id)
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
    if body.fighter_id not in (str(match.fighter1_id), str(match.fighter2_id)):
        raise HTTPException(400, "Fighter not in this match")

    # Validate amount
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    if body.amount < MIN_BET:
        raise HTTPException(400, f"Minimum bet is {MIN_BET} SKR")

    # Validate side
    if body.side.upper() not in ("A", "B"):
        raise HTTPException(400, "side must be 'A' or 'B'")

    # Calculate current odds snapshot
    active = [b for b in match.bets if b.status == BetStatus.ACTIVE]
    total  = sum(float(b.amount) for b in active) + body.amount
    fighter_pool = (
        sum(float(b.amount) for b in active if b.fighter_id == body.fighter_id)
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
            raise HTTPException(502, f"On-chain bet placement failed: {exc}")
    else:
        # Match not yet created on-chain — warn and proceed DB-only
        logger.warning(
            "Match %s has no on_chain_match_pda — recording bet DB-only", match.id
        )

    # ── Persist to DB ─────────────────────────────────────────────────────────
    bet = Bet(
        user_id=user.id,
        match_id=match.id,
        fighter_id=body.fighter_id,
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
    bet.fighter = match.fighter1 if str(body.fighter_id) == str(match.fighter1_id) else match.fighter2

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
