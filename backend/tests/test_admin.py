"""Test /api/admin endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bet, BetStatus, Fighter, Match, MatchStatus, User


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
    assert "different" in resp.json()["detail"].lower()


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
    client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    db: AsyncSession,
    cleanup,
):
    """Cancelling a match cancels all active bets."""
    f1, _ = fighters

    # Place a bet first (using regular user client)
    bet_resp = await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f1.id),
        "amount": 5.0,
    })
    assert bet_resp.status_code == 200
    bet_id = bet_resp.json()["id"]

    # Cancel the match (as admin)
    cancel_resp = await admin_client.post(
        f"/api/admin/matches/{match_with_stream.id}/cancel"
    )
    assert cancel_resp.status_code == 200

    # Verify the bet is cancelled
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

    # Verify f1 bettor won with correct payout (15/10 * 10 = 15)
    result = await db.execute(
        select(Bet)
        .where(Bet.match_id == live_match.id, Bet.fighter_id == f1.id)
    )
    won_bet = result.scalar_one()
    assert won_bet.status == BetStatus.WON
    assert float(won_bet.payout) == 15.0  # 10 * (15/10)

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
    assert "winner" in resp.json()["detail"].lower()


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
    assert "upcoming" in resp.json()["detail"].lower()


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
