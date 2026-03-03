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
from app.exceptions import (
    FighterNotFoundError,
    MatchNotFoundError,
    ValidationError,
    InvalidMatchStateError,
    InvalidSavestateError,
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
            raise FighterNotFoundError(str(fid))

    if body.fighter1_id == body.fighter2_id:
        raise ValidationError("Fighter 1 and Fighter 2 must be different")

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
        select(Match)
        .where(Match.id == match_id)
        .options(
            selectinload(Match.stream),
            selectinload(Match.fighter1).selectinload(Fighter.agent),
            selectinload(Match.fighter2).selectinload(Fighter.agent),
        )
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise MatchNotFoundError(str(match_id))
    if match.status != MatchStatus.UPCOMING:
        raise InvalidMatchStateError(
            str(match_id), match.status.value, "upcoming"
        )

    if not match.savestate_path:
        raise InvalidSavestateError("", "Match has no savestate_path configured")

    # Snapshot values needed after DB session closes
    savestate_path = match.savestate_path
    best_of = match.best_of
    match_id_str = str(match_id)

    # ✅ FIX: Wire uploaded agents into match execution
    # Determine agent ID, checkpoint path, AND architecture for both fighters
    p1_agent_id = match.p1_agent  # default from match
    p2_agent_id = match.p2_agent  # default from match
    p1_checkpoint_path: str | None = None
    p2_checkpoint_path: str | None = None
    p1_architecture: str | None = None
    p2_architecture: str | None = None

    # P1 agent resolution: custom uploaded agent takes priority over built-in
    if match.fighter1:
        if match.fighter1.agent_id and match.fighter1.agent:
            # Custom uploaded agent
            p1_agent_id = f"custom_{match.fighter1.agent.slug}"
            p1_checkpoint_path = match.fighter1.agent.checkpoint_path
            p1_architecture = match.fighter1.agent.architecture
            logger.info(f"P1 using custom agent: {p1_agent_id} ({p1_architecture}) from {p1_checkpoint_path}")
        elif match.fighter1.agent_architecture in ("random", "cpu", "lstm", "obj_belief", "disc_rssm", "transformer"):
            # Built-in agent with valid ID
            p1_agent_id = match.fighter1.agent_architecture
            logger.info(f"P1 using built-in agent: {p1_agent_id}")
        else:
            # Invalid or architecture-only value (like "mlp") - default to random
            logger.info(f"P1 using built-in agent: {p1_agent_id}")

    # P2 agent resolution: custom uploaded agent takes priority over built-in
    if match.fighter2:
        if match.fighter2.agent_id and match.fighter2.agent:
            # Custom uploaded agent
            p2_agent_id = f"custom_{match.fighter2.agent.slug}"
            p2_checkpoint_path = match.fighter2.agent.checkpoint_path
            p2_architecture = match.fighter2.agent.architecture
            logger.info(f"P2 using custom agent: {p2_agent_id} ({p2_architecture}) from {p2_checkpoint_path}")
        elif match.fighter2.agent_architecture in ("random", "cpu", "lstm", "obj_belief", "disc_rssm", "transformer"):
            # Built-in agent with valid ID
            p2_agent_id = match.fighter2.agent_architecture
            logger.info(f"P2 using built-in agent: {p2_agent_id}")
        else:
            # Invalid or architecture-only value (like "mlp") - default to random
            logger.info(f"P2 using built-in agent: {p2_agent_id}")

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
                p1_agent_id=p1_agent_id,
                p2_agent_id=p2_agent_id,
                p1_checkpoint_path=p1_checkpoint_path,
                p2_checkpoint_path=p2_checkpoint_path,
                p1_architecture=p1_architecture,
                p2_architecture=p2_architecture,
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
async def settle_match_endpoint(
    match_id: UUID,
    winner_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually settle a live match.

    ✅ FIX: Now uses shared settlement service to ensure consistency with auto-settlement.
    Previously this endpoint reimplemented settlement inline, which:
    - Stopped the runner BEFORE reading round counters (lost data)
    - Could drift from auto-settlement logic over time

    Now both manual and auto settlement use the same code path.
    """
    # Validate match exists and is in correct state
    result = await db.execute(
        select(Match).where(Match.id == match_id)
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")
    if match.status != MatchStatus.LIVE:
        raise HTTPException(400, f"Match is {match.status.value}, cannot settle")
    if winner_id not in (match.fighter1_id, match.fighter2_id):
        raise HTTPException(400, "Winner must be one of the fighters in this match")

    # Determine winner player number (1 or 2)
    winner_player = 1 if winner_id == match.fighter1_id else 2

    # Use shared settlement service (reads round counters BEFORE stopping runner)
    from app.services.settlement import settle_match
    await settle_match(str(match_id), winner_player)

    # Stop runner after settlement (settlement.py reads round data from it)
    from app.services.match_runner import stop_match as runner_stop
    await runner_stop(str(match_id))

    # ✅ FIX: Re-fetch match with eager-loaded bets to avoid DetachedInstanceError
    # The original match object is detached after settle_match() uses its own session
    result = await db.execute(
        select(Match)
        .where(Match.id == match_id)
        .options(selectinload(Match.bets))
    )
    match = result.scalar_one_or_none()
    active_bets = [b for b in match.bets if b.status in (BetStatus.WON, BetStatus.LOST)]
    total_pool = sum(float(b.amount) for b in active_bets)

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
