from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Fighter, Match, MatchStatus, Stream
from app.services.queue_loop import QueueLoopManager, _LOOP_FIGHTER_NAMES, _ordered_pair_sequence


@pytest.mark.asyncio
async def test_queue_loop_seeds_all_12_directed_pairs(
    db: AsyncSession,
    monkeypatch,
):
    fighters: list[Fighter] = []
    fighter_specs = [
        ("Scorpion", "scorpion", "disc_rssm"),
        ("Sub-Zero", "sub-zero", "transformer"),
        ("Sonya", "sonya", "obj_belief"),
        ("Cage", "cage", "lstm"),
    ]
    for idx, (name, slug, arch) in enumerate(fighter_specs, start=1):
        fighters.append(
            Fighter(
                name=name,
                slug=slug,
                character="MK4",
                character_id=idx,
                llm_model="test",
                agent_architecture=arch,
            )
        )
    db.add_all(fighters)
    await db.flush()

    seed = Match(
        fighter1_id=fighters[0].id,
        fighter2_id=fighters[1].id,
        p1_agent="disc_rssm",
        p2_agent="transformer",
        status=MatchStatus.UPCOMING,
        scheduled_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        label="MK4-Classic",
        savestate_path="/tmp/p1p2state.st",
        best_of=3,
        on_chain_match_id=1,
        on_chain_match_pda="seed-pda",
    )
    db.add(seed)
    await db.flush()
    db.add(Stream(match_id=seed.id))
    await db.commit()

    seq = {"next": 10}

    async def _fake_create_match_on_chain(*, fighter1_name: str, fighter2_name: str):
        seq["next"] += 1
        return seq["next"], f"pda-{fighter1_name}-{fighter2_name}-{seq['next']}"

    monkeypatch.setattr(
        "app.services.on_chain_match.create_match_on_chain",
        _fake_create_match_on_chain,
    )

    manager = QueueLoopManager()
    await manager._tick_once()

    fighter_ids = [f.id for f in fighters]
    result = await db.execute(
        select(Match).where(
            Match.status.in_([MatchStatus.UPCOMING, MatchStatus.LIVE]),
            Match.fighter1_id.in_(fighter_ids),
            Match.fighter2_id.in_(fighter_ids),
        )
    )
    active = list(result.scalars().all())
    assert len(active) == 12

    ordered = {f.name: f for f in fighters}
    expected_pairs = {
        (a.id, b.id)
        for a, b in _ordered_pair_sequence([ordered[n] for n in _LOOP_FIGHTER_NAMES])
    }
    actual_pairs = {(m.fighter1_id, m.fighter2_id) for m in active}
    assert actual_pairs == expected_pairs


@pytest.mark.asyncio
async def test_matches_queue_countdown_metadata_for_head(
    client: AsyncClient,
    db: AsyncSession,
):
    f1 = Fighter(
        name="Queue A",
        slug="queue-a",
        character="MK4",
        character_id=1,
        llm_model="test",
    )
    f2 = Fighter(
        name="Queue B",
        slug="queue-b",
        character="MK4",
        character_id=2,
        llm_model="test",
    )
    db.add_all([f1, f2])
    await db.flush()

    now = datetime.now(timezone.utc)
    m1 = Match(
        fighter1_id=f1.id,
        fighter2_id=f2.id,
        p1_agent="random",
        p2_agent="random",
        status=MatchStatus.UPCOMING,
        scheduled_at=now + timedelta(seconds=40),
        label="A",
    )
    m2 = Match(
        fighter1_id=f2.id,
        fighter2_id=f1.id,
        p1_agent="random",
        p2_agent="random",
        status=MatchStatus.UPCOMING,
        scheduled_at=now + timedelta(seconds=80),
        label="B",
    )
    db.add_all([m1, m2])
    await db.commit()

    resp = await client.get("/api/matches/?status=upcoming")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    by_id = {r["id"]: r for r in rows}

    one = by_id[str(m1.id)]
    two = by_id[str(m2.id)]

    assert one["queue_position"] == 1
    assert isinstance(one["queue_starts_at"], str)
    assert one["queue_countdown_seconds"] is not None
    assert 0 <= int(one["queue_countdown_seconds"]) <= 40

    assert two["queue_position"] == 2
    assert two["queue_starts_at"] is None
    assert two["queue_countdown_seconds"] is None


@pytest.mark.asyncio
async def test_matches_hides_next_match_countdown_while_live_exists(
    client: AsyncClient,
    db: AsyncSession,
):
    f1 = Fighter(
        name="Live A",
        slug="live-a",
        character="MK4",
        character_id=11,
        llm_model="test",
    )
    f2 = Fighter(
        name="Live B",
        slug="live-b",
        character="MK4",
        character_id=12,
        llm_model="test",
    )
    f3 = Fighter(
        name="Live C",
        slug="live-c",
        character="MK4",
        character_id=13,
        llm_model="test",
    )
    db.add_all([f1, f2, f3])
    await db.flush()

    now = datetime.now(timezone.utc)
    live = Match(
        fighter1_id=f1.id,
        fighter2_id=f2.id,
        p1_agent="random",
        p2_agent="random",
        status=MatchStatus.LIVE,
        scheduled_at=now - timedelta(seconds=30),
        started_at=now - timedelta(seconds=5),
        label="LIVE",
    )
    upcoming = Match(
        fighter1_id=f2.id,
        fighter2_id=f3.id,
        p1_agent="random",
        p2_agent="random",
        status=MatchStatus.UPCOMING,
        scheduled_at=now + timedelta(seconds=35),
        label="NEXT",
    )
    db.add_all([live, upcoming])
    await db.commit()

    resp = await client.get("/api/matches/?status=upcoming,live")
    assert resp.status_code == 200, resp.text
    rows = {r["id"]: r for r in resp.json()}

    next_row = rows[str(upcoming.id)]
    assert next_row["queue_position"] == 1
    assert next_row["queue_starts_at"] is None
    assert next_row["queue_countdown_seconds"] is None
