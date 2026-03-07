"""Automated loop queue manager for the 4-fighter dev arena.

Behavior:
- Maintains one active (UPCOMING/LIVE) match per directed fighter pair.
- For 4 fighters, keeps a 12-state directed cycle (no self-fights).
- When no match is LIVE, arms a 60s countdown for queue head.
- Locks the head match on-chain at T-1s and starts it at T.

This manager is safe to run in multiple Uvicorn workers:
it uses a non-blocking file lock so only one process executes each tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.engine import async_session
from app.db.models import Fighter, Match, MatchStatus, Stream, StreamStatus

logger = logging.getLogger(__name__)

_LOOP_FIGHTER_NAMES: tuple[str, str, str, str] = (
    "Scorpion",
    "Sub-Zero",
    "Sonya",
    "Cage",
)

_LOOP_AGENT_POLICY: dict[str, str] = {
    "Scorpion": "disc_rssm",   # RSSM
    "Sub-Zero": "transformer",  # Transformer
    "Sonya": "obj_belief",      # Belief
    "Cage": "lstm",             # LSTM
}

_VALID_BUILTIN_AGENT_IDS = frozenset(
    {"random", "cpu", "lstm", "obj_belief", "disc_rssm", "transformer"}
)

# Variety-first 12-state directed loop:
# round 1 (offset +1): A->B, B->C, C->D, D->A
# round 2 (offset +2): A->C, B->D, C->A, D->B
# round 3 (offset +3): A->D, B->A, C->B, D->C
_PAIR_SEQUENCE_INDEXES: tuple[tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 0),
    (0, 2), (1, 3), (2, 0), (3, 1),
    (0, 3), (1, 0), (2, 1), (3, 2),
)
_QUEUE_MATCH_LABEL = "MK4-Classic"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_slug_for_savestate(slug: str) -> str:
    return slug.strip().lower().replace(" ", "").replace("-", "")


def _discover_savestates() -> list[str]:
    from app.services.emulator import M64P_ROOT

    repo_root = Path(__file__).resolve().parents[3]
    roots = [
        repo_root / "training" / "data" / "savestates",
        M64P_ROOT / "data" / "savestates",
    ]
    out: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for st in sorted(root.rglob("*.st")):
            out.append(str(st))
    return out


def _resolve_match_savestate(f1: Fighter, f2: Fighter) -> str | None:
    savestates = _discover_savestates()
    if not savestates:
        return None

    by_file = {Path(path).name.lower(): path for path in savestates}
    s1 = _normalize_slug_for_savestate(f1.slug)
    s2 = _normalize_slug_for_savestate(f2.slug)
    preferred = [
        f"p1p2_{s1}_{s2}.st",
        "p1p2state.st",
        "kai_arcade_p1p2.st",
        f"p1p2_{s2}_{s1}.st",
    ]
    for filename in preferred:
        path = by_file.get(filename.lower())
        if path:
            return path
    return savestates[0]


def _pair_key(f1_id: UUID | None, f2_id: UUID | None) -> tuple[UUID | None, UUID | None]:
    return (f1_id, f2_id)


def _ordered_pair_sequence(fighters: list[Fighter]) -> list[tuple[Fighter, Fighter]]:
    return [(fighters[a], fighters[b]) for a, b in _PAIR_SEQUENCE_INDEXES]


def _agent_for_fighter(fighter: Fighter) -> str:
    policy = _LOOP_AGENT_POLICY.get(fighter.name)
    if policy in _VALID_BUILTIN_AGENT_IDS:
        return policy
    arch = (fighter.agent_architecture or "").strip().lower()
    if arch in _VALID_BUILTIN_AGENT_IDS:
        return arch
    return "random"


class _FileLeaderLock:
    def __init__(self, lock_file: str):
        self._path = Path(lock_file)
        self._fh = None

    def try_acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._fh is None:
            self._fh = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def release(self) -> None:
        if self._fh is None:
            return
        with contextlib.suppress(Exception):
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)

    def close(self) -> None:
        self.release()
        if self._fh is not None:
            with contextlib.suppress(Exception):
                self._fh.close()
            self._fh = None


@dataclass
class _HeadSnapshot:
    match_id: UUID
    scheduled_at: datetime
    on_chain_match_pda: str | None


class QueueLoopManager:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._leader_lock = _FileLeaderLock(settings.queue_leader_lock_file)
        self._missing_fighters_warned = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="imk-queue-loop")
        logger.info("Queue loop manager started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._leader_lock.close()
        logger.info("Queue loop manager stopped")

    async def _run(self) -> None:
        tick_seconds = max(0.2, float(settings.queue_tick_seconds))
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            try:
                if self._leader_lock.try_acquire():
                    try:
                        await self._tick_once()
                    finally:
                        self._leader_lock.release()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Queue loop tick failed")

            elapsed = time.monotonic() - t0
            sleep_for = max(0.0, tick_seconds - elapsed)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

    async def _tick_once(self) -> None:
        fighters = await self._load_loop_fighters()
        if len(fighters) != len(_LOOP_FIGHTER_NAMES):
            return

        await self._ensure_directed_cycle_rows(fighters)
        head = await self._prepare_head_snapshot(fighters)
        if head is None:
            return

        now = _utc_now()
        remaining = (_as_utc(head.scheduled_at) - now).total_seconds()

        if remaining <= float(settings.queue_lock_before_start_seconds):
            await self._lock_head_match(head)

        if remaining <= 0:
            await self._start_match(head.match_id)

    async def _load_loop_fighters(self) -> list[Fighter]:
        async with async_session() as db:
            result = await db.execute(
                select(Fighter).where(Fighter.name.in_(_LOOP_FIGHTER_NAMES))
            )
            rows = result.scalars().all()
        by_name = {f.name: f for f in rows}
        ordered: list[Fighter] = []
        for name in _LOOP_FIGHTER_NAMES:
            fighter = by_name.get(name)
            if fighter is None:
                if not self._missing_fighters_warned:
                    logger.warning(
                        "Queue loop paused: missing fighter '%s' (needs all %s)",
                        name,
                        ", ".join(_LOOP_FIGHTER_NAMES),
                    )
                    self._missing_fighters_warned = True
                return []
            ordered.append(fighter)
        self._missing_fighters_warned = False
        return ordered

    async def _list_active_loop_matches(self, fighter_ids: Iterable[UUID]) -> list[Match]:
        ids = list(fighter_ids)
        async with async_session() as db:
            result = await db.execute(
                select(Match)
                .where(
                    Match.status.in_([MatchStatus.UPCOMING, MatchStatus.LIVE]),
                    Match.fighter1_id.in_(ids),
                    Match.fighter2_id.in_(ids),
                )
                .order_by(Match.scheduled_at.asc(), Match.created_at.asc())
            )
            return list(result.scalars().all())

    async def _ensure_directed_cycle_rows(self, fighters: list[Fighter]) -> None:
        pair_sequence = _ordered_pair_sequence(fighters)
        fighter_ids = [f.id for f in fighters]
        await self._normalize_active_loop_labels(fighter_ids)
        active_rows = await self._list_active_loop_matches(fighter_ids)

        counts: dict[tuple[UUID | None, UUID | None], int] = {}
        for m in active_rows:
            key = _pair_key(m.fighter1_id, m.fighter2_id)
            counts[key] = counts.get(key, 0) + 1

        missing: list[tuple[Fighter, Fighter]] = []
        for f1, f2 in pair_sequence:
            key = _pair_key(f1.id, f2.id)
            if counts.get(key, 0) == 0:
                missing.append((f1, f2))

        if not missing:
            return

        now = _utc_now()
        upcoming = [m for m in active_rows if m.status == MatchStatus.UPCOMING]
        tail_time = max((_as_utc(m.scheduled_at) for m in upcoming), default=now)

        for f1, f2 in missing:
            tail_time = max(tail_time, now) + timedelta(seconds=1)
            created = await self._create_loop_match(f1=f1, f2=f2, scheduled_at=tail_time)
            if created:
                logger.info(
                    "Queue loop enqueued %s vs %s at tail",
                    f1.name,
                    f2.name,
                )

    async def _normalize_active_loop_labels(self, fighter_ids: list[UUID]) -> None:
        async with async_session() as db:
            result = await db.execute(
                select(Match).where(
                    Match.status.in_([MatchStatus.UPCOMING, MatchStatus.LIVE]),
                    Match.fighter1_id.in_(fighter_ids),
                    Match.fighter2_id.in_(fighter_ids),
                )
            )
            rows = result.scalars().all()
            dirty = False
            for m in rows:
                if m.label != _QUEUE_MATCH_LABEL:
                    m.label = _QUEUE_MATCH_LABEL
                    dirty = True
            if dirty:
                await db.commit()

    async def _latest_pair_template(self, f1_id: UUID, f2_id: UUID) -> tuple[int, str | None]:
        async with async_session() as db:
            latest_pair = await db.execute(
                select(Match)
                .where(Match.fighter1_id == f1_id, Match.fighter2_id == f2_id)
                .order_by(Match.created_at.desc())
                .limit(1)
            )
            pair_match = latest_pair.scalar_one_or_none()
            if pair_match:
                return (
                    int(pair_match.best_of or 3),
                    pair_match.savestate_path,
                )

            latest_any = await db.execute(
                select(Match)
                .where(Match.savestate_path.is_not(None))
                .order_by(Match.created_at.desc())
                .limit(1)
            )
            any_match = latest_any.scalar_one_or_none()
            if any_match:
                return (
                    3,
                    any_match.savestate_path,
                )

        return (3, None)

    async def _create_loop_match(self, *, f1: Fighter, f2: Fighter, scheduled_at: datetime) -> bool:
        best_of, savestate_path = await self._latest_pair_template(f1.id, f2.id)
        if not savestate_path:
            savestate_path = _resolve_match_savestate(f1, f2)

        async with async_session() as db:
            precheck = await db.execute(
                select(Match)
                .where(
                    Match.status.in_([MatchStatus.UPCOMING, MatchStatus.LIVE]),
                    Match.fighter1_id == f1.id,
                    Match.fighter2_id == f2.id,
                )
                .limit(1)
            )
            if precheck.scalar_one_or_none() is not None:
                return False

        from app.services.on_chain_match import create_match_on_chain

        try:
            on_chain_id, on_chain_pda = await create_match_on_chain(
                fighter1_name=f1.name,
                fighter2_name=f2.name,
            )
        except Exception:
            logger.exception("Queue loop create_match_on_chain failed for %s vs %s", f1.name, f2.name)
            return False

        async with async_session() as db:
            active_same = await db.execute(
                select(Match)
                .where(
                    Match.status.in_([MatchStatus.UPCOMING, MatchStatus.LIVE]),
                    Match.fighter1_id == f1.id,
                    Match.fighter2_id == f2.id,
                )
                .limit(1)
            )
            if active_same.scalar_one_or_none() is not None:
                from app.services.on_chain_match import cancel_match_on_chain

                with contextlib.suppress(Exception):
                    await cancel_match_on_chain(on_chain_pda)
                return False

            try:
                match = Match(
                    fighter1_id=f1.id,
                    fighter2_id=f2.id,
                    p1_agent=_agent_for_fighter(f1),
                    p2_agent=_agent_for_fighter(f2),
                    status=MatchStatus.UPCOMING,
                    label=_QUEUE_MATCH_LABEL,
                    scheduled_at=_as_utc(scheduled_at),
                    best_of=int(best_of or 3),
                    savestate_path=savestate_path,
                    on_chain_match_id=on_chain_id,
                    on_chain_match_pda=on_chain_pda,
                )
                db.add(match)
                await db.flush()
                db.add(Stream(match_id=match.id))
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                logger.exception(
                    "Queue loop failed to persist DB match for on-chain match %s",
                    on_chain_pda,
                )
                from app.services.on_chain_match import cancel_match_on_chain

                with contextlib.suppress(Exception):
                    await cancel_match_on_chain(on_chain_pda)
                return False

    async def _prepare_head_snapshot(self, fighters: list[Fighter]) -> _HeadSnapshot | None:
        fighter_ids = [f.id for f in fighters]

        async with async_session() as db:
            result = await db.execute(
                select(Match)
                .where(
                    Match.status.in_([MatchStatus.UPCOMING, MatchStatus.LIVE]),
                    Match.fighter1_id.in_(fighter_ids),
                    Match.fighter2_id.in_(fighter_ids),
                )
                .order_by(Match.scheduled_at.asc(), Match.created_at.asc())
            )
            active = list(result.scalars().all())

            live = [m for m in active if m.status == MatchStatus.LIVE]
            if live:
                return None

            upcoming = [m for m in active if m.status == MatchStatus.UPCOMING]
            if not upcoming:
                return None

            now = _utc_now()
            head = upcoming[0]
            head_time = _as_utc(head.scheduled_at)

            # Head has reached/passed its previous marker: arm a fresh 60s countdown
            # and preserve queue order by re-spacing all UPCOMING matches.
            if head_time <= now:
                # If on-chain is already LOCKED, this head should start now; do not
                # re-arm countdown (that would cause an infinite reset loop).
                if head.on_chain_match_pda:
                    try:
                        from app.services import solana_tx

                        rpc = solana_tx.DEVNET_RPC if settings.use_devnet else solana_tx.MAINNET_RPC
                        state = await solana_tx.fetch_match(head.on_chain_match_pda, rpc)
                        if state is not None and int(state.get("status", -1)) == 1:
                            return _HeadSnapshot(
                                match_id=head.id,
                                scheduled_at=head_time,
                                on_chain_match_pda=head.on_chain_match_pda,
                            )
                    except Exception:
                        logger.exception(
                            "Queue loop failed reading on-chain status for head %s",
                            head.id,
                        )

                base = now + timedelta(seconds=int(settings.queue_match_countdown_seconds))
                for idx, m in enumerate(upcoming):
                    m.scheduled_at = base + timedelta(seconds=idx)
                await db.commit()
                logger.info(
                    "Queue loop armed countdown: head=%s starts_at=%s",
                    head.id,
                    base.isoformat(),
                )
                return None

            return _HeadSnapshot(
                match_id=head.id,
                scheduled_at=head_time,
                on_chain_match_pda=head.on_chain_match_pda,
            )

    async def _lock_head_match(self, head: _HeadSnapshot) -> None:
        if not head.on_chain_match_pda:
            return
        from app.services.on_chain_match import lock_match_on_chain

        try:
            await lock_match_on_chain(head.on_chain_match_pda)
        except Exception:
            logger.exception(
                "Queue loop lock failed for head match %s (pda=%s)",
                head.match_id,
                head.on_chain_match_pda,
            )

    async def _start_match(self, match_id: UUID) -> None:
        async with async_session() as db:
            result = await db.execute(
                select(Match)
                .where(Match.id == match_id)
                .options(selectinload(Match.stream))
            )
            match = result.scalar_one_or_none()
            if match is None:
                return
            if match.status != MatchStatus.UPCOMING:
                return

            if match.on_chain_match_pda:
                from app.services.on_chain_match import lock_match_on_chain

                try:
                    await lock_match_on_chain(match.on_chain_match_pda)
                except Exception:
                    logger.exception("Queue loop final lock failed for match %s", match_id)
                    from app.services.match_cancel import cancel_match_by_id_contract_first

                    with contextlib.suppress(Exception):
                        await cancel_match_by_id_contract_first(
                            match_id,
                            stream_status=StreamStatus.ERROR,
                            reason="queue_loop_lock_failed",
                        )
                    return

            if not match.savestate_path:
                logger.error("Queue loop cannot start match %s: missing savestate_path", match_id)
                from app.services.match_cancel import cancel_match_by_id_contract_first

                with contextlib.suppress(Exception):
                    await cancel_match_by_id_contract_first(
                        match_id,
                        stream_status=StreamStatus.ERROR,
                        reason="queue_loop_missing_savestate",
                    )
                return

            savestate_path = match.savestate_path
            p1_agent_id = match.p1_agent or "random"
            p2_agent_id = match.p2_agent or "random"
            best_of = int(match.best_of or 3)
            match.status = MatchStatus.LIVE
            match.started_at = _utc_now()
            if match.stream:
                match.stream.status = StreamStatus.STARTING
            await db.commit()

        asyncio.create_task(
            self._launch_runner(
                match_id=str(match_id),
                savestate_path=savestate_path,
                p1_agent_id=p1_agent_id,
                p2_agent_id=p2_agent_id,
                best_of=best_of,
            )
        )

    async def _launch_runner(
        self,
        *,
        match_id: str,
        savestate_path: str,
        p1_agent_id: str,
        p2_agent_id: str,
        best_of: int,
    ) -> None:
        from app.services.match_runner import get_runner, start_match as runner_start
        from app.services.match_cancel import cancel_match_by_id_contract_first

        try:
            await runner_start(
                match_id=match_id,
                savestate_path=savestate_path,
                p1_agent_id=p1_agent_id,
                p2_agent_id=p2_agent_id,
                best_of=best_of,
            )
        except Exception:
            logger.exception("Queue loop runner start failed for match %s", match_id)
            with contextlib.suppress(Exception):
                await cancel_match_by_id_contract_first(
                    match_id,
                    stream_status=StreamStatus.ERROR,
                    reason="queue_loop_runner_start_failed",
                )
            return

        runner = get_runner(match_id)
        if not runner:
            return

        async with async_session() as db:
            result = await db.execute(
                select(Match).where(Match.id == UUID(match_id)).options(selectinload(Match.stream))
            )
            match = result.scalar_one_or_none()
            if not match:
                return
            match.emulator_instance_id = runner.instance_id
            if match.stream:
                match.stream.status = StreamStatus.LIVE
            await db.commit()


queue_loop_manager = QueueLoopManager()
