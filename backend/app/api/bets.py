from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_current_user, get_db
from app.db.models import Bet, BetStatus, Match, MatchStatus, User
from app.schemas.bet import BetCreate, BetOut

router = APIRouter(prefix="/bets", tags=["bets"])

MIN_BET = 0.01  # Minimum bet in SOL


def _bet_to_out(bet: Bet) -> BetOut:
    """Build BetOut with fighter/opponent names from loaded relationships."""
    fighter_name = ""
    opponent_name = ""

    if bet.fighter:
        fighter_name = bet.fighter.name

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


@router.post("/", response_model=BetOut)
async def place_bet(
    body: BetCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate match exists and is upcoming or live
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
    if body.fighter_id not in (match.fighter1_id, match.fighter2_id):
        raise HTTPException(400, "Fighter not in this match")

    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    if body.amount < MIN_BET:
        raise HTTPException(400, f"Minimum bet is {MIN_BET} SOL")

    # Calculate current odds for snapshot
    active = [b for b in match.bets if b.status == BetStatus.ACTIVE]
    total = sum(float(b.amount) for b in active) + body.amount
    fighter_pool = (
        sum(float(b.amount) for b in active if b.fighter_id == body.fighter_id)
        + body.amount
    )
    odds = round(total / fighter_pool, 4) if fighter_pool > 0 else 2.0

    bet = Bet(
        user_id=user.id,
        match_id=body.match_id,
        fighter_id=body.fighter_id,
        amount=body.amount,
        odds_at_placement=odds,
    )
    db.add(bet)
    await db.commit()
    await db.refresh(bet)

    # Load relationships for the response
    bet.match = match
    if body.fighter_id == match.fighter1_id:
        bet.fighter = match.fighter1
    else:
        bet.fighter = match.fighter2

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
