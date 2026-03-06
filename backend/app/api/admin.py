import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
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

_VALID_BUILTIN_AGENT_IDS = frozenset(
    {"random", "cpu", "lstm", "obj_belief", "disc_rssm", "transformer"}
)


def _resolve_fighter_agent(
    fighter: Fighter | None,
    fallback_agent_id: str | None,
    slot: str,
) -> tuple[str, str | None, str | None]:
    """
    Resolve the runtime agent for a fighter slot.

    Priority:
    1) fighter.custom agent (agent_id + agent relation)
    2) fighter.agent_architecture (built-in)
    3) fallback agent from match payload/state (built-in only)
    """
    agent_id = fallback_agent_id or "random"

    if fighter is None:
        if agent_id not in _VALID_BUILTIN_AGENT_IDS:
            return "random", None, None
        return agent_id, None, None

    if fighter.agent_id is not None:
        if fighter.agent is None:
            raise ValidationError(
                f"{slot} fighter '{fighter.name}' has agent_id but missing linked Agent record"
            )
        checkpoint = fighter.agent.checkpoint_path
        if not checkpoint:
            raise ValidationError(
                f"{slot} fighter '{fighter.name}' custom agent has no checkpoint_path"
            )
        if not Path(checkpoint).is_file():
            raise ValidationError(
                f"{slot} fighter '{fighter.name}' checkpoint not found on server: {checkpoint}"
            )
        return f"custom_{fighter.agent.slug}", checkpoint, fighter.agent.architecture

    arch = fighter.agent_architecture
    if arch in _VALID_BUILTIN_AGENT_IDS:
        return arch, None, None
    if arch:
        logger.warning(
            "%s fighter '%s' has invalid built-in architecture '%s'; using random",
            slot,
            fighter.name,
            arch,
        )
        return "random", None, None

    if agent_id not in _VALID_BUILTIN_AGENT_IDS:
        return "random", None, None
    return agent_id, None, None


@router.post("/matches", response_model=MatchOut)
async def create_match(
    body: MatchCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.fighter1_id == body.fighter2_id:
        raise ValidationError("Fighter 1 and Fighter 2 must be different")

    # Verify both fighters exist and resolve fixed fighter policy agent IDs.
    fighter1_result = await db.execute(
        select(Fighter)
        .where(Fighter.id == body.fighter1_id)
        .options(selectinload(Fighter.agent))
    )
    fighter1 = fighter1_result.scalar_one_or_none()
    if fighter1 is None:
        raise FighterNotFoundError(str(body.fighter1_id))

    fighter2_result = await db.execute(
        select(Fighter)
        .where(Fighter.id == body.fighter2_id)
        .options(selectinload(Fighter.agent))
    )
    fighter2 = fighter2_result.scalar_one_or_none()
    if fighter2 is None:
        raise FighterNotFoundError(str(body.fighter2_id))

    p1_agent_id, _, _ = _resolve_fighter_agent(
        fighter=fighter1,
        fallback_agent_id=body.p1_agent,
        slot="P1",
    )
    p2_agent_id, _, _ = _resolve_fighter_agent(
        fighter=fighter2,
        fallback_agent_id=body.p2_agent,
        slot="P2",
    )

    match = Match(
        fighter1_id=body.fighter1_id,
        fighter2_id=body.fighter2_id,
        scheduled_at=body.scheduled_at,
        label=body.label,
        savestate_path=body.savestate_path,
        p1_agent=p1_agent_id,
        p2_agent=p2_agent_id,
        best_of=body.best_of,
    )
    db.add(match)
    await db.flush()  # generate match.id before creating stream

    # Create associated stream record
    stream = Stream(match_id=match.id)
    db.add(stream)

    await db.commit()
    await db.refresh(match, attribute_names=["fighter1", "fighter2", "bets", "stream"])

    # ── Create match on-chain ─────────────────────────────────────────────────
    # Fire-and-forget background task so the HTTP response isn't blocked by RPC.
    async def _create_on_chain(match_id_str: str, fighter1_name: str, fighter2_name: str) -> None:
        from solders.pubkey import Pubkey
        from app.config import settings
        from app.services import solana_tx
        from app.services.admin_keypair import get_admin_keypair
        from app.db.engine import async_session
        import hashlib

        try:
            admin_kp = get_admin_keypair()
        except ValueError as exc:
            logger.warning("Admin keypair not set — skipping on-chain create_match: %s", exc)
            return

        rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC

        # Derive model hashes from fighter names (deterministic, auditable)
        model_a_hash = hashlib.sha256(fighter1_name.encode()).digest()
        model_b_hash = hashlib.sha256(fighter2_name.encode()).digest()

        try:
            blockhash = await solana_tx.get_recent_blockhash(rpc)
            prog_pk = Pubkey.from_string(settings.betting_program_id)
            cfg = await solana_tx.fetch_config(settings.betting_program_id, rpc)
            match_counter = int(cfg["match_counter"])
            logger.info("On-chain match_counter before create: %d", match_counter)

            tx = solana_tx.build_create_match_ix(
                admin_keypair=admin_kp,
                skr_mint_str=settings.skr_mint,
                match_counter=match_counter,
                model_a_hash=model_a_hash,
                model_b_hash=model_b_hash,
                blockhash=blockhash,
                program_id_str=settings.betting_program_id,
            )
            sig = await solana_tx.send_and_confirm_transaction(
                tx, rpc, retries=settings.solana_confirm_retries
            )
            logger.info("create_match on-chain tx: %s (counter=%d)", sig, match_counter)

            # Derive the match PDA that was created
            prog_pk = Pubkey.from_string(settings.betting_program_id)
            match_pda = solana_tx.derive_match_pda(match_counter, prog_pk)
            match_pda_str = str(match_pda)
            if not await solana_tx.account_exists(match_pda_str, rpc):
                logger.error("create_match confirmed but PDA missing: %s", match_pda_str)
                return

            # Store on-chain IDs in DB
            async with async_session() as fresh_db:
                from sqlalchemy import select as _sel
                res = await fresh_db.execute(_sel(Match).where(Match.id == match_id_str))
                m = res.scalar_one_or_none()
                if m:
                    m.on_chain_match_id  = match_counter
                    m.on_chain_match_pda = match_pda_str
                    await fresh_db.commit()
                    logger.info(
                        "Match %s linked to on-chain PDA %s (id=%d)",
                        match_id_str, match_pda_str, match_counter,
                    )

        except Exception as exc:
            logger.error("on-chain create_match failed for %s: %s", match_id_str, exc, exc_info=True)

    import asyncio as _asyncio
    _asyncio.create_task(_create_on_chain(
        str(match.id),
        match.fighter1.name if match.fighter1 else "fighter1",
        match.fighter2.name if match.fighter2 else "fighter2",
    ))

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

    # Resolve exact runtime agents from fighter policy and keep match metadata in sync.
    p1_agent_id, p1_checkpoint_path, p1_architecture = _resolve_fighter_agent(
        fighter=match.fighter1,
        fallback_agent_id=match.p1_agent,
        slot="P1",
    )
    p2_agent_id, p2_checkpoint_path, p2_architecture = _resolve_fighter_agent(
        fighter=match.fighter2,
        fallback_agent_id=match.p2_agent,
        slot="P2",
    )
    if match.p1_agent != p1_agent_id:
        match.p1_agent = p1_agent_id
    if match.p2_agent != p2_agent_id:
        match.p2_agent = p2_agent_id

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
    from app.services.settlement import OnChainSettlementError, settle_match
    try:
        await settle_match(str(match_id), winner_player)
    except OnChainSettlementError as exc:
        raise HTTPException(502, f"On-chain settlement failed: {exc}")

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
