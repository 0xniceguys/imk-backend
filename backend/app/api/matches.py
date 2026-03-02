from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_db
from app.db.models import Bet, BetStatus, Match, MatchStatus
from app.schemas.match import MatchOut, OddsOut

router = APIRouter(prefix="/matches", tags=["matches"])


def _compute_odds(bets: list[Bet], fighter1_id: UUID, fighter2_id: UUID) -> OddsOut:
    """Compute parimutuel odds from active bets."""
    active = [b for b in bets if b.status == BetStatus.ACTIVE]
    total = sum(float(b.amount) for b in active)
    f1_pool = sum(float(b.amount) for b in active if b.fighter_id == fighter1_id)
    f2_pool = total - f1_pool

    if total == 0:
        return OddsOut(
            fighter1_odds=2.0,
            fighter2_odds=2.0,
            fighter1_pool_pct=0.5,
            fighter2_pool_pct=0.5,
            total_pool=0.0,
            active_bets=0,
        )

    return OddsOut(
        fighter1_odds=round(total / f1_pool, 4) if f1_pool > 0 else 0.0,
        fighter2_odds=round(total / f2_pool, 4) if f2_pool > 0 else 0.0,
        fighter1_pool_pct=round(f1_pool / total, 4),
        fighter2_pool_pct=round(f2_pool / total, 4),
        total_pool=round(total, 6),
        active_bets=len(active),
    )


def _match_to_out(match: Match) -> MatchOut:
    odds = _compute_odds(match.bets, match.fighter1_id, match.fighter2_id)
    stream_url = None
    if match.stream and match.stream.hls_path:
        stream_url = f"/api/stream/{match.id}/frame"

    return MatchOut(
        id=match.id,
        fighter1=match.fighter1,
        fighter2=match.fighter2,
        status=match.status.value,
        label=match.label,
        scheduled_at=match.scheduled_at,
        started_at=match.started_at,
        completed_at=match.completed_at,
        winner_id=match.winner_id,
        stream_url=stream_url,
        odds=odds,
        best_of=match.best_of,
        current_round=match.current_round,
        rounds_won_p1=match.rounds_won_p1,
        rounds_won_p2=match.rounds_won_p2,
        betting_open=match.status == MatchStatus.UPCOMING,
        created_at=match.created_at,
    )


@router.get("/", response_model=list[MatchOut])
async def list_matches(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Match)
        .options(
            selectinload(Match.fighter1),
            selectinload(Match.fighter2),
            selectinload(Match.bets),
            selectinload(Match.stream),
        )
        .order_by(Match.scheduled_at.desc())
    )

    if status:
        statuses = [s.strip() for s in status.split(",")]
        query = query.where(Match.status.in_(statuses))

    result = await db.execute(query)
    matches = result.scalars().all()
    return [_match_to_out(m) for m in matches]


@router.get("/{match_id}", response_model=MatchOut)
async def get_match(match_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(
            selectinload(Match.fighter1),
            selectinload(Match.fighter2),
            selectinload(Match.bets),
            selectinload(Match.stream),
        )
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    return _match_to_out(match)


@router.get("/{match_id}/odds", response_model=OddsOut)
async def get_odds(match_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.bets))
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    return _compute_odds(match.bets, match.fighter1_id, match.fighter2_id)
