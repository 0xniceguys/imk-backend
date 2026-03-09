from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_db
from app.db.models import Bet, BetStatus, Match, MatchStatus
from app.exceptions import MatchNotFoundError
from app.schemas.match import MatchBetFeedOut, MatchOut, OddsOut

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
            fighter1_pool=0.0,
            fighter2_pool=0.0,
            total_pool=0.0,
            active_bets=0,
        )

    return OddsOut(
        fighter1_odds=round(total / f1_pool, 4) if f1_pool > 0 else 0.0,
        fighter2_odds=round(total / f2_pool, 4) if f2_pool > 0 else 0.0,
        fighter1_pool_pct=round(f1_pool / total, 4),
        fighter2_pool_pct=round(f2_pool / total, 4),
        fighter1_pool=round(f1_pool, 6),
        fighter2_pool=round(f2_pool, 6),
        total_pool=round(total, 6),
        active_bets=len(active),
    )


def _mask_wallet(wallet: str | None) -> str:
    if not wallet:
        return "Unknown"
    value = wallet.strip()
    if len(value) <= 10:
        return value
    return f"{value[:4]}...{value[-4:]}"


def _queue_positions_for_matches(matches: list[Match]) -> dict[UUID, int]:
    """Queue mapping for arena UI.

    Rules:
    - All LIVE matches map to queue_position=0.
    - UPCOMING matches map to 1..N by scheduled_at ascending.
    - COMPLETED/CANCELLED are excluded.
    """
    out: dict[UUID, int] = {}

    live_matches = [m for m in matches if m.status == MatchStatus.LIVE]
    for m in live_matches:
        out[m.id] = 0

    upcoming = [m for m in matches if m.status == MatchStatus.UPCOMING]
    upcoming.sort(key=lambda m: (m.scheduled_at, m.created_at))
    for idx, m in enumerate(upcoming, start=1):
        out[m.id] = idx

    return out


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _match_to_out(
    match: Match,
    queue_position: int | None = None,
    now: datetime | None = None,
) -> MatchOut:
    # fighter IDs may be None if fighter was deleted
    f1_id = match.fighter1_id or match.id  # fallback prevents div-by-zero
    f2_id = match.fighter2_id or match.id
    odds = _compute_odds(match.bets, f1_id, f2_id)
    now_utc = _as_utc(now or datetime.now(timezone.utc))

    queue_starts_at: datetime | None = None
    queue_countdown_seconds: int | None = None
    # Always provide countdown for the #1 upcoming match — the client syncs
    # its local timer to the backend's scheduled_at (set by queue_loop).
    if queue_position == 1 and match.status == MatchStatus.UPCOMING:
        starts_at = _as_utc(match.scheduled_at)
        queue_starts_at = starts_at
        queue_countdown_seconds = max(0, int((starts_at - now_utc).total_seconds()))

    stream_url = None
    if match.status == MatchStatus.LIVE:
        from app.services.match_runner import get_runner
        runner = get_runner(str(match.id))
        if runner:
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
        queue_position=queue_position,
        queue_starts_at=queue_starts_at,
        queue_countdown_seconds=queue_countdown_seconds,
        created_at=match.created_at,
    )


@router.get("/", response_model=list[MatchOut])
async def list_matches(
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    safe_limit = max(1, min(limit, 200))
    query = (
        select(Match)
        .options(
            selectinload(Match.fighter1),
            selectinload(Match.fighter2),
            selectinload(Match.bets),
            selectinload(Match.stream),
        )
        .order_by(Match.scheduled_at.desc())
        .limit(safe_limit)
    )

    if status:
        raw_statuses = [s.strip() for s in status.split(",")]
        # Convert to MatchStatus enum values, skip any unrecognized strings
        valid_statuses = []
        for s in raw_statuses:
            try:
                valid_statuses.append(MatchStatus(s))
            except ValueError:
                pass  # ignore unknown status values like "running"
        if valid_statuses:
            query = query.where(Match.status.in_(valid_statuses))

    result = await db.execute(query)
    matches = result.scalars().all()
    queue_positions = _queue_positions_for_matches(matches)
    now = datetime.now(timezone.utc)
    return [
        _match_to_out(
            m,
            queue_position=queue_positions.get(m.id),
            now=now,
        )
        for m in matches
    ]


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
        raise MatchNotFoundError(str(match_id))

    queue_position: int | None = None
    if match.status in (MatchStatus.LIVE, MatchStatus.UPCOMING):
        queue_rows = await db.execute(
            select(Match).where(Match.status.in_([MatchStatus.LIVE, MatchStatus.UPCOMING]))
        )
        queue_matches = queue_rows.scalars().all()
        queue_position = _queue_positions_for_matches(queue_matches).get(match.id)

    return _match_to_out(
        match,
        queue_position=queue_position,
        now=datetime.now(timezone.utc),
    )


@router.get("/{match_id}/bet-feed", response_model=list[MatchBetFeedOut])
async def get_match_bet_feed(
    match_id: UUID,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    safe_limit = max(1, min(limit, 100))
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(
            selectinload(Match.bets).selectinload(Bet.user),
            selectinload(Match.fighter1),
            selectinload(Match.fighter2),
        )
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise MatchNotFoundError(str(match_id))

    if match.status in (MatchStatus.UPCOMING, MatchStatus.LIVE):
        bets = [b for b in match.bets if b.status == BetStatus.ACTIVE]
    else:
        bets = list(match.bets)

    bets.sort(key=lambda b: b.placed_at, reverse=True)

    out: list[MatchBetFeedOut] = []
    for bet in bets[:safe_limit]:
        side = "A" if bet.fighter_id == match.fighter1_id else "B"
        if side == "A":
            fighter_name = match.fighter1.name if match.fighter1 else "Fighter 1"
        else:
            fighter_name = match.fighter2.name if match.fighter2 else "Fighter 2"

        out.append(
            MatchBetFeedOut(
                wallet_masked=_mask_wallet(bet.user.wallet_address if bet.user else None),
                side=side,
                fighter_name=fighter_name,
                amount=float(bet.amount),
                status=bet.status.value if hasattr(bet.status, "value") else str(bet.status),
                placed_at=bet.placed_at,
            )
        )
    return out


@router.get("/{match_id}/odds", response_model=OddsOut)
async def get_odds(match_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.bets))
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise MatchNotFoundError(str(match_id))
    return _compute_odds(match.bets, match.fighter1_id, match.fighter2_id)
