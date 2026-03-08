"""Test /api/matches endpoints."""

import pytest
from httpx import AsyncClient

from app.db.models import Bet, BetStatus, Fighter, Match, User


@pytest.mark.asyncio
async def test_list_matches(client: AsyncClient, match_with_stream: Match, cleanup):
    """GET /api/matches/ returns matches with nested fighter data."""
    resp = await client.get("/api/matches/")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # Find our test match
    match_data = next((m for m in data if m["id"] == str(match_with_stream.id)), None)
    assert match_data is not None
    assert match_data["status"] == "upcoming"
    assert match_data["label"] == "Test Match"

    # Verify nested fighter objects
    assert "fighter1" in match_data
    assert "fighter2" in match_data
    assert match_data["fighter1"]["id"] is not None
    assert match_data["fighter2"]["id"] is not None

    # Verify odds are present (defaults for no bets)
    assert "odds" in match_data
    odds = match_data["odds"]
    assert odds["fighter1_odds"] == 2.0
    assert odds["fighter2_odds"] == 2.0
    assert odds["total_pool"] == 0.0
    assert odds["active_bets"] == 0


@pytest.mark.asyncio
async def test_list_matches_filter_by_status(
    client: AsyncClient, match_with_stream: Match, live_match: Match, cleanup
):
    """GET /api/matches/?status=live filters correctly."""
    resp = await client.get("/api/matches/?status=live")
    assert resp.status_code == 200
    data = resp.json()
    statuses = {m["status"] for m in data}
    assert "upcoming" not in statuses
    assert all(s == "live" for s in statuses)


@pytest.mark.asyncio
async def test_list_matches_filter_multi_status(
    client: AsyncClient, match_with_stream: Match, live_match: Match, cleanup
):
    """GET /api/matches/?status=upcoming,live returns both."""
    resp = await client.get("/api/matches/?status=upcoming,live")
    assert resp.status_code == 200
    data = resp.json()
    ids = {m["id"] for m in data}
    assert str(match_with_stream.id) in ids
    assert str(live_match.id) in ids


@pytest.mark.asyncio
async def test_get_match_by_id(
    client: AsyncClient, match_with_stream: Match, cleanup
):
    """GET /api/matches/{id} returns full match details."""
    resp = await client.get(f"/api/matches/{match_with_stream.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(match_with_stream.id)
    assert data["status"] == "upcoming"
    assert data["fighter1"]["name"] is not None
    assert data["fighter2"]["name"] is not None


@pytest.mark.asyncio
async def test_get_match_not_found(client: AsyncClient):
    """GET /api/matches/{bad_id} returns 404."""
    resp = await client.get("/api/matches/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_odds(
    client: AsyncClient, match_with_stream: Match, cleanup
):
    """GET /api/matches/{id}/odds returns default odds when no bets."""
    resp = await client.get(f"/api/matches/{match_with_stream.id}/odds")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fighter1_odds"] == 2.0
    assert data["fighter2_odds"] == 2.0
    assert data["fighter1_pool_pct"] == 0.5
    assert data["fighter2_pool_pct"] == 0.5
    assert data["total_pool"] == 0.0
    assert data["active_bets"] == 0


@pytest.mark.asyncio
async def test_match_schema_fields(
    client: AsyncClient, match_with_stream: Match, cleanup
):
    """Verify MatchOut schema has all expected fields."""
    resp = await client.get(f"/api/matches/{match_with_stream.id}")
    data = resp.json()
    expected_keys = {
        "id", "fighter1", "fighter2", "status", "label",
        "scheduled_at", "started_at", "completed_at",
        "winner_id", "stream_url", "odds", "created_at",
        "best_of", "current_round", "rounds_won_p1", "rounds_won_p2",
        "betting_open",
    }
    assert expected_keys.issubset(set(data.keys())), f"Missing: {expected_keys - set(data.keys())}"
    # Verify round defaults
    assert data["best_of"] == 3
    assert data["current_round"] == 1
    assert data["rounds_won_p1"] == 0
    assert data["rounds_won_p2"] == 0
    # Upcoming match should have betting open
    assert data["betting_open"] is True


@pytest.mark.asyncio
async def test_match_bet_feed_returns_masked_recent_bets(
    client: AsyncClient,
    db,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    f1, f2 = fighters
    user1 = User(
        privy_user_id="feed-u1",
        wallet_address="AbcdEFGHiJkLmNoPqRsTuVwXyZ1234567890",
        email="u1@test.local",
        display_name="User1",
    )
    user2 = User(
        privy_user_id="feed-u2",
        wallet_address="ZYXwVuTsRqPoNmLkJiHgFeDcBa9876543210",
        email="u2@test.local",
        display_name="User2",
    )
    db.add_all([user1, user2])
    await db.flush()

    b1 = Bet(
        user_id=user1.id,
        match_id=match_with_stream.id,
        fighter_id=f1.id,
        amount=123.0,
        currency="SKR",
        odds_at_placement=1.5,
        status=BetStatus.ACTIVE,
        on_chain_side="A",
    )
    b2 = Bet(
        user_id=user2.id,
        match_id=match_with_stream.id,
        fighter_id=f2.id,
        amount=222.0,
        currency="SKR",
        odds_at_placement=1.7,
        status=BetStatus.ACTIVE,
        on_chain_side="B",
    )
    db.add_all([b1, b2])
    await db.commit()

    resp = await client.get(f"/api/matches/{match_with_stream.id}/bet-feed")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["wallet_masked"].count("...") == 1
    assert rows[1]["wallet_masked"].count("...") == 1
    assert rows[0]["side"] in ("A", "B")
    assert rows[1]["side"] in ("A", "B")
    assert rows[0]["fighter_name"] in (f1.name, f2.name)
    assert rows[1]["fighter_name"] in (f1.name, f2.name)
    assert rows[0]["status"] == "active"
