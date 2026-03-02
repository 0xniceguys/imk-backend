import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.dependencies import get_db, require_admin
from app.db.models import (
    Bet,
    BetStatus,
    Fighter,
    Match,
    MatchStatus,
    Stream,
    StreamStatus,
    User,
)
from app.schemas.fighter import FighterCreate, FighterOut
from app.schemas.match import MatchCreate, MatchOut

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/matches", response_model=MatchOut)
async def create_match(
    body: MatchCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Verify both fighters exist
    for fid in (body.fighter1_id, body.fighter2_id):
        result = await db.execute(select(Fighter).where(Fighter.id == fid))
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, f"Fighter {fid} not found")

    if body.fighter1_id == body.fighter2_id:
        raise HTTPException(400, "Fighter 1 and Fighter 2 must be different")

    match = Match(
        fighter1_id=body.fighter1_id,
        fighter2_id=body.fighter2_id,
        scheduled_at=body.scheduled_at,
        label=body.label,
        savestate_path=body.savestate_path,
        p1_agent=body.p1_agent,
        p2_agent=body.p2_agent,
        best_of=body.best_of,
    )
    db.add(match)
    await db.flush()  # generate match.id before creating stream

    # Create associated stream record
    stream = Stream(match_id=match.id)
    db.add(stream)

    await db.commit()
    await db.refresh(match, attribute_names=["fighter1", "fighter2", "bets", "stream"])

    from app.api.matches import _match_to_out
    return _match_to_out(match)


@router.post("/matches/{match_id}/start")
async def start_match(
    match_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match).where(Match.id == match_id).options(selectinload(Match.stream))
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    if match.status != MatchStatus.UPCOMING:
        raise HTTPException(400, f"Match is {match.status.value}, cannot start")

    if not match.savestate_path:
        raise HTTPException(400, "Match has no savestate_path configured")

    # Snapshot values needed after DB session closes
    savestate_path = match.savestate_path
    p1_agent = match.p1_agent
    p2_agent = match.p2_agent
    best_of = match.best_of
    match_id_str = str(match_id)

    # Mark as LIVE and commit — THEN release DB connection before launching emulator.
    # If we hold the session open during the 3-10s emulator launch, the connection
    # pool is exhausted and every other endpoint (including GET /api/matches/) hangs.
    match.status = MatchStatus.LIVE
    match.started_at = datetime.now(timezone.utc)
    if match.stream:
        match.stream.status = StreamStatus.STARTING
    await db.commit()
    # Let SQLAlchemy return the connection to the pool now.
    await db.close()

    # Launch the emulator + bridge + streaming loops in a background task so
    # this HTTP response can return immediately.
    async def _launch_in_background() -> None:
        from app.services.match_runner import start_match as runner_start
        from app.db.engine import async_session

        try:
            await runner_start(
                match_id=match_id_str,
                savestate_path=savestate_path,
                p1_agent_id=p1_agent,
                p2_agent_id=p2_agent,
                best_of=best_of,
            )
        except Exception as e:
            logger.error("Failed to start emulator for match %s: %s", match_id_str, e)
            # Roll back DB status on launch failure using a fresh session
            async with async_session() as fresh_db:
                res = await fresh_db.execute(
                    select(Match).where(Match.id == match_id).options(selectinload(Match.stream))
                )
                m = res.scalar_one_or_none()
                if m:
                    m.status = MatchStatus.UPCOMING
                    m.started_at = None
                    if m.stream:
                        m.stream.status = StreamStatus.IDLE
                    await fresh_db.commit()
            return

        # Update emulator instance ID in a fresh session
        from app.services.match_runner import get_runner
        live_runner = get_runner(match_id_str)
        if live_runner:
            async with async_session() as fresh_db:
                res = await fresh_db.execute(
                    select(Match).where(Match.id == match_id).options(selectinload(Match.stream))
                )
                m = res.scalar_one_or_none()
                if m:
                    m.emulator_instance_id = live_runner.instance_id
                    if m.stream:
                        m.stream.status = StreamStatus.LIVE
                    await fresh_db.commit()

    asyncio.create_task(_launch_in_background())
    return {"status": "starting", "match_id": match_id_str}


@router.post("/matches/{match_id}/stop")
async def stop_match(
    match_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Stop the emulator for a live match without settling bets."""
    from app.services.match_runner import stop_match as runner_stop

    await runner_stop(str(match_id))

    result = await db.execute(
        select(Match).where(Match.id == match_id).options(selectinload(Match.stream))
    )
    match = result.scalar_one_or_none()
    if match:
        match.status = MatchStatus.UPCOMING  # back to upcoming so it can be restarted
        match.started_at = None
        if match.stream:
            match.stream.status = StreamStatus.STOPPED
        await db.commit()

    return {"status": "stopped", "match_id": str(match_id)}


@router.post("/matches/{match_id}/cancel")
async def cancel_match(
    match_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.bets), selectinload(Match.stream))
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    if match.status == MatchStatus.COMPLETED:
        raise HTTPException(400, "Cannot cancel completed match")

    # Stop runner if live
    from app.services.match_runner import stop_match as runner_stop
    await runner_stop(str(match_id))

    match.status = MatchStatus.CANCELLED
    if match.stream:
        match.stream.status = StreamStatus.STOPPED

    # Refund all active bets
    for bet in match.bets:
        if bet.status == BetStatus.ACTIVE:
            bet.status = BetStatus.CANCELLED

    await db.commit()
    return {"status": "cancelled", "match_id": str(match_id)}


@router.post("/matches/{match_id}/settle")
async def settle_match(
    match_id: UUID,
    winner_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.bets), selectinload(Match.stream))
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    if match.status != MatchStatus.LIVE:
        raise HTTPException(400, f"Match is {match.status.value}, cannot settle")
    if winner_id not in (match.fighter1_id, match.fighter2_id):
        raise HTTPException(400, "Winner must be one of the fighters in this match")

    # Stop runner
    from app.services.match_runner import stop_match as runner_stop
    await runner_stop(str(match_id))

    match.status = MatchStatus.COMPLETED
    match.winner_id = winner_id
    match.completed_at = datetime.now(timezone.utc)
    if match.stream:
        match.stream.status = StreamStatus.STOPPED

    # Update fighter stats (skip NULL fighter IDs from old matches)
    for fid in (match.fighter1_id, match.fighter2_id):
        if fid is None:
            continue
        f_result = await db.execute(select(Fighter).where(Fighter.id == fid))
        fighter = f_result.scalar_one_or_none()
        if fighter is None:
            continue
        fighter.matches_played += 1
        if fid == winner_id:
            fighter.matches_won += 1

    # Settle bets — parimutuel payout
    active_bets = [b for b in match.bets if b.status == BetStatus.ACTIVE]
    total_pool = sum(float(b.amount) for b in active_bets)
    winner_pool = sum(
        float(b.amount) for b in active_bets if b.fighter_id == winner_id
    )

    now = datetime.now(timezone.utc)
    for bet in active_bets:
        bet.settled_at = now
        if bet.fighter_id == winner_id:
            bet.status = BetStatus.WON
            if winner_pool > 0:
                bet.payout = round(float(bet.amount) * (total_pool / winner_pool), 6)
            else:
                bet.payout = float(bet.amount)
        else:
            bet.status = BetStatus.LOST
            bet.payout = 0.0

    await db.commit()
    return {
        "status": "settled",
        "match_id": str(match_id),
        "winner_id": str(winner_id),
        "total_pool": total_pool,
        "bets_settled": len(active_bets),
    }


@router.post("/fighters", response_model=FighterOut)
async def create_fighter(
    body: FighterCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    fighter = Fighter(**body.model_dump())
    db.add(fighter)
    await db.commit()
    await db.refresh(fighter)
    return fighter
