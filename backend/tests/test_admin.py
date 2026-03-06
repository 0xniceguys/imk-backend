"""Test /api/admin endpoints."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, Bet, BetStatus, Fighter, Match, MatchStatus, User


def _error_text(resp) -> str:
    data = resp.json()
    if "detail" in data:
        return str(data["detail"])
    return str(data.get("error", {}).get("message", ""))


@pytest.mark.asyncio
async def test_create_fighter(
    admin_client: AsyncClient, cleanup
):
    """POST /api/admin/fighters creates a new fighter."""
    resp = await admin_client.post("/api/admin/fighters", json={
        "name": "Test Fighter",
        "slug": "test-fighter",
        "character": "MK4",
        "character_id": 5,
        "llm_model": "random",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "Test Fighter"
    assert data["slug"] == "test-fighter"
    assert data["matches_played"] == 0
    assert data["win_rate"] == 0.0


@pytest.mark.asyncio
async def test_create_match(
    admin_client: AsyncClient,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """POST /api/admin/matches creates a match with stream."""
    f1, f2 = fighters
    resp = await admin_client.post("/api/admin/matches", json={
        "fighter1_id": str(f1.id),
        "fighter2_id": str(f2.id),
        "scheduled_at": "2026-03-01T12:00:00Z",
        "label": "Admin Test Match",
        "p1_agent": "random",
        "p2_agent": "random",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "upcoming"
    assert data["label"] == "Admin Test Match"
    assert data["fighter1"]["id"] == str(f1.id)
    assert data["fighter2"]["id"] == str(f2.id)


@pytest.mark.asyncio
async def test_create_match_uses_fighter_assigned_custom_agents(
    admin_client: AsyncClient,
    fighters: tuple[Fighter, Fighter],
    db: AsyncSession,
    tmp_path,
    cleanup,
):
    """Match agent fields are derived from fighters, not raw payload agent IDs."""
    f1, f2 = fighters

    ckpt1 = tmp_path / "f1.onnx"
    ckpt2 = tmp_path / "f2.onnx"
    ckpt1.write_bytes(b"onnx")
    ckpt2.write_bytes(b"onnx")

    agent1 = Agent(
        name=f"Agent One {uuid.uuid4().hex[:8]}",
        slug=f"agent-one-{uuid.uuid4().hex[:8]}",
        architecture="disc_rssm",
        checkpoint_path=str(ckpt1),
        file_size_bytes=4,
    )
    agent2 = Agent(
        name=f"Agent Two {uuid.uuid4().hex[:8]}",
        slug=f"agent-two-{uuid.uuid4().hex[:8]}",
        architecture="transformer",
        checkpoint_path=str(ckpt2),
        file_size_bytes=4,
    )
    db.add_all([agent1, agent2])
    await db.flush()

    f1_db = (await db.execute(select(Fighter).where(Fighter.id == f1.id))).scalar_one()
    f2_db = (await db.execute(select(Fighter).where(Fighter.id == f2.id))).scalar_one()
    f1_db.agent_id = agent1.id
    f1_db.agent_architecture = None
    f2_db.agent_id = agent2.id
    f2_db.agent_architecture = None
    await db.commit()

    resp = await admin_client.post(
        "/api/admin/matches",
        json={
            "fighter1_id": str(f1.id),
            "fighter2_id": str(f2.id),
            "scheduled_at": "2026-03-01T12:00:00Z",
            "label": "Fixed Policy Match",
            "p1_agent": "random",
            "p2_agent": "cpu",
        },
    )
    assert resp.status_code == 200, resp.text

    match_id = uuid.UUID(resp.json()["id"])
    match = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
    assert match.p1_agent == f"custom_{agent1.slug}"
    assert match.p2_agent == f"custom_{agent2.slug}"


@pytest.mark.asyncio
async def test_create_match_rejects_missing_custom_checkpoint(
    admin_client: AsyncClient,
    fighters: tuple[Fighter, Fighter],
    db: AsyncSession,
    cleanup,
):
    """Match creation fails fast when fighter's custom checkpoint path is invalid."""
    f1, f2 = fighters
    missing_ckpt = "/tmp/does-not-exist-imk-checkpoint.onnx"
    agent = Agent(
        name=f"Broken Agent {uuid.uuid4().hex[:8]}",
        slug=f"broken-agent-{uuid.uuid4().hex[:8]}",
        architecture="lstm",
        checkpoint_path=missing_ckpt,
        file_size_bytes=123,
    )
    db.add(agent)
    await db.flush()

    f1_db = (await db.execute(select(Fighter).where(Fighter.id == f1.id))).scalar_one()
    f1_db.agent_id = agent.id
    f1_db.agent_architecture = None
    await db.commit()

    create_resp = await admin_client.post(
        "/api/admin/matches",
        json={
            "fighter1_id": str(f1.id),
            "fighter2_id": str(f2.id),
            "scheduled_at": "2026-03-01T12:00:00Z",
            "label": "Missing Checkpoint",
            "savestate_path": "/tmp/ok.savestate",
        },
    )
    assert create_resp.status_code == 400
    assert "checkpoint not found" in _error_text(create_resp).lower()

@pytest.mark.asyncio
async def test_create_match_same_fighter_rejected(
    admin_client: AsyncClient,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """POST /api/admin/matches rejects same fighter vs self."""
    f1, _ = fighters
    resp = await admin_client.post("/api/admin/matches", json={
        "fighter1_id": str(f1.id),
        "fighter2_id": str(f1.id),
        "scheduled_at": "2026-03-01T12:00:00Z",
    })
    assert resp.status_code == 400
    assert "different" in _error_text(resp).lower()


@pytest.mark.asyncio
async def test_create_match_missing_fighter(
    admin_client: AsyncClient,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """POST /api/admin/matches returns 404 for nonexistent fighter."""
    f1, _ = fighters
    resp = await admin_client.post("/api/admin/matches", json={
        "fighter1_id": str(f1.id),
        "fighter2_id": "00000000-0000-0000-0000-000000000000",
        "scheduled_at": "2026-03-01T12:00:00Z",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_match(
    admin_client: AsyncClient,
    match_with_stream: Match,
    cleanup,
):
    """POST /api/admin/matches/{id}/cancel cancels an upcoming match."""
    resp = await admin_client.post(
        f"/api/admin/matches/{match_with_stream.id}/cancel"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_match_cancels_bets(
    admin_client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    db: AsyncSession,
    test_user: User,
    cleanup,
):
    """Cancelling a match cancels all active bets."""
    f1, _ = fighters

    # Insert an active bet directly (no auth/client override contention in this test).
    bet = Bet(
        user_id=test_user.id,
        match_id=match_with_stream.id,
        fighter_id=f1.id,
        amount=5.0,
        odds_at_placement=1.5,
        status=BetStatus.ACTIVE,
    )
    db.add(bet)
    await db.commit()
    await db.refresh(bet)
    bet_id = bet.id

    # Cancel the match (as admin)
    cancel_resp = await admin_client.post(
        f"/api/admin/matches/{match_with_stream.id}/cancel"
    )
    assert cancel_resp.status_code == 200

    # Verify the bet is cancelled
    db.expire_all()
    result = await db.execute(select(Bet).where(Bet.id == bet_id))
    bet = result.scalar_one()
    assert bet.status == BetStatus.CANCELLED


@pytest.mark.asyncio
async def test_settle_match(
    admin_client: AsyncClient,
    live_match: Match,
    fighters: tuple[Fighter, Fighter],
    test_user: User,
    db: AsyncSession,
    cleanup,
):
    """POST /api/admin/matches/{id}/settle settles bets correctly."""
    f1, f2 = fighters

    # Insert bets directly (betting API now rejects live match bets)
    bet1 = Bet(
        user_id=test_user.id, match_id=live_match.id,
        fighter_id=f1.id, amount=10.0, odds_at_placement=1.5,
    )
    bet2 = Bet(
        user_id=test_user.id, match_id=live_match.id,
        fighter_id=f2.id, amount=5.0, odds_at_placement=3.0,
    )
    db.add_all([bet1, bet2])
    await db.commit()

    # Settle with f1 as winner
    resp = await admin_client.post(
        f"/api/admin/matches/{live_match.id}/settle",
        params={"winner_id": str(f1.id)},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "settled"
    assert data["total_pool"] == 15.0
    assert data["bets_settled"] == 2

    # Expire cached state so we see fresh data from the settle endpoint's commit
    db.expire_all()

    # Verify f1 bettor won with contract fee math:
    # pool=15, fee=5%=0.75, payout_pool=14.25, winner_pool=10 => payout=14.25
    result = await db.execute(
        select(Bet)
        .where(Bet.match_id == live_match.id, Bet.fighter_id == f1.id)
    )
    won_bet = result.scalar_one()
    assert won_bet.status == BetStatus.WON
    assert float(won_bet.payout) == 14.25

    # Verify f2 bettor lost
    result = await db.execute(
        select(Bet)
        .where(Bet.match_id == live_match.id, Bet.fighter_id == f2.id)
    )
    lost_bet = result.scalar_one()
    assert lost_bet.status == BetStatus.LOST
    assert float(lost_bet.payout) == 0.0


@pytest.mark.asyncio
async def test_settle_updates_fighter_stats(
    admin_client: AsyncClient,
    live_match: Match,
    fighters: tuple[Fighter, Fighter],
    db: AsyncSession,
    cleanup,
):
    """Settlement increments fighter stats."""
    f1, f2 = fighters
    initial_f1_played = f1.matches_played
    initial_f2_played = f2.matches_played

    resp = await admin_client.post(
        f"/api/admin/matches/{live_match.id}/settle",
        params={"winner_id": str(f1.id)},
    )
    assert resp.status_code == 200

    # Re-query from DB (objects are detached from the fixture session)
    f1_result = await db.execute(select(Fighter).where(Fighter.id == f1.id))
    f1_fresh = f1_result.scalar_one()
    f2_result = await db.execute(select(Fighter).where(Fighter.id == f2.id))
    f2_fresh = f2_result.scalar_one()
    assert f1_fresh.matches_played == initial_f1_played + 1
    assert f1_fresh.matches_won >= 1
    assert f2_fresh.matches_played == initial_f2_played + 1


@pytest.mark.asyncio
async def test_settle_wrong_winner(
    admin_client: AsyncClient,
    live_match: Match,
    db: AsyncSession,
    cleanup,
):
    """Settling with a fighter not in the match fails."""
    resp = await admin_client.post(
        f"/api/admin/matches/{live_match.id}/settle",
        params={"winner_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 400
    assert "winner" in _error_text(resp).lower()


@pytest.mark.asyncio
async def test_settle_not_live_match(
    admin_client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """Settling an UPCOMING match fails."""
    f1, _ = fighters
    resp = await admin_client.post(
        f"/api/admin/matches/{match_with_stream.id}/settle",
        params={"winner_id": str(f1.id)},
    )
    assert resp.status_code == 400
    assert "upcoming" in _error_text(resp).lower()


@pytest.mark.asyncio
async def test_admin_requires_auth(unauthed_client: AsyncClient):
    """Admin endpoints return 401 without auth."""
    resp = await unauthed_client.post("/api/admin/fighters", json={
        "name": "Test",
        "slug": "test",
        "character": "MK4",
        "character_id": 1,
        "llm_model": "random",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_requires_admin_role(client: AsyncClient):
    """Regular user (non-admin) gets 403 on admin endpoints."""
    resp = await client.post("/api/admin/fighters", json={
        "name": "Test",
        "slug": "test",
        "character": "MK4",
        "character_id": 1,
        "llm_model": "random",
    })
    assert resp.status_code == 403
