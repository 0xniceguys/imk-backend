"""Test /api/bets endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bet, BetStatus, Fighter, Match, User


@pytest.mark.asyncio
async def test_place_bet_on_upcoming_match(
    client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """POST /api/bets/ places a bet on fighter1 of an upcoming match."""
    f1, _ = fighters
    resp = await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f1.id),
        "amount": 1.5,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["match_id"] == str(match_with_stream.id)
    assert data["fighter_id"] == str(f1.id)
    assert data["amount"] == 1.5
    assert data["status"] == "active"
    assert data["fighter_name"] == f1.name
    assert data["opponent_name"] != ""
    assert data["odds_at_placement"] > 0


@pytest.mark.asyncio
async def test_place_bet_on_live_match_rejected(
    client: AsyncClient,
    live_match: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """POST /api/bets/ rejects betting on a live match (betting closes at match start)."""
    _, f2 = fighters
    resp = await client.post("/api/bets/", json={
        "match_id": str(live_match.id),
        "fighter_id": str(f2.id),
        "amount": 2.0,
    })
    assert resp.status_code == 400
    assert "betting closed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_place_bet_wrong_fighter(
    client: AsyncClient,
    match_with_stream: Match,
    db: AsyncSession,
    cleanup,
):
    """POST /api/bets/ rejects bet on a fighter not in the match."""
    # Create a third fighter not in the match
    from app.db.models import Fighter
    import uuid
    rogue = Fighter(
        name=f"Rogue-{uuid.uuid4().hex[:6]}",
        slug=f"rogue-{uuid.uuid4().hex[:6]}",
        character="MK4",
        character_id=99,
        llm_model="random",
    )
    db.add(rogue)
    await db.commit()
    await db.refresh(rogue)

    resp = await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(rogue.id),
        "amount": 1.0,
    })
    assert resp.status_code == 400
    assert "not in this match" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_place_bet_negative_amount(
    client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """POST /api/bets/ rejects negative amount."""
    f1, _ = fighters
    resp = await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f1.id),
        "amount": -1.0,
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_place_bet_zero_amount(
    client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """POST /api/bets/ rejects zero amount."""
    f1, _ = fighters
    resp = await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f1.id),
        "amount": 0,
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_place_bet_below_minimum(
    client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """POST /api/bets/ rejects bets below 0.01 SOL minimum."""
    f1, _ = fighters
    resp = await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f1.id),
        "amount": 0.005,
    })
    assert resp.status_code == 400
    assert "minimum bet" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_place_bet_nonexistent_match(
    client: AsyncClient, cleanup
):
    """POST /api/bets/ returns 404 for nonexistent match."""
    resp = await client.post("/api/bets/", json={
        "match_id": "00000000-0000-0000-0000-000000000000",
        "fighter_id": "00000000-0000-0000-0000-000000000001",
        "amount": 1.0,
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_my_bets_empty(client: AsyncClient, cleanup):
    """GET /api/bets/mine returns empty list when no bets placed."""
    resp = await client.get("/api/bets/mine")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_my_bets_returns_placed_bet(
    client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """GET /api/bets/mine includes a bet we just placed."""
    f1, _ = fighters
    place_resp = await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f1.id),
        "amount": 3.0,
    })
    assert place_resp.status_code == 200
    bet_id = place_resp.json()["id"]

    mine_resp = await client.get("/api/bets/mine")
    assert mine_resp.status_code == 200
    data = mine_resp.json()
    ids = [b["id"] for b in data]
    assert bet_id in ids


@pytest.mark.asyncio
async def test_my_bets_requires_auth(unauthed_client: AsyncClient):
    """GET /api/bets/mine returns 401 without auth."""
    resp = await unauthed_client.get("/api/bets/mine")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_place_bet_requires_auth(unauthed_client: AsyncClient):
    """POST /api/bets/ returns 401 without auth."""
    resp = await unauthed_client.post("/api/bets/", json={
        "match_id": "00000000-0000-0000-0000-000000000000",
        "fighter_id": "00000000-0000-0000-0000-000000000001",
        "amount": 1.0,
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_odds_update_after_bet(
    client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """Odds change after placing a bet."""
    f1, f2 = fighters

    # Place bet on f1
    await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f1.id),
        "amount": 10.0,
    })

    # Check odds — f1 should have lower odds (more money on that side)
    resp = await client.get(f"/api/matches/{match_with_stream.id}/odds")
    data = resp.json()
    assert data["total_pool"] == 10.0
    assert data["active_bets"] == 1
    assert data["fighter1_pool_pct"] == 1.0  # all bets on f1
    assert data["fighter2_pool_pct"] == 0.0

    # Place bet on f2
    await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f2.id),
        "amount": 5.0,
    })

    resp = await client.get(f"/api/matches/{match_with_stream.id}/odds")
    data = resp.json()
    assert data["total_pool"] == 15.0
    assert data["active_bets"] == 2
    # f1 has 10/15 = 0.6667, f2 has 5/15 = 0.3333
    assert abs(data["fighter1_pool_pct"] - 0.6667) < 0.01
    assert abs(data["fighter2_pool_pct"] - 0.3333) < 0.01
    # f1 odds = 15/10 = 1.5, f2 odds = 15/5 = 3.0
    assert abs(data["fighter1_odds"] - 1.5) < 0.01
    assert abs(data["fighter2_odds"] - 3.0) < 0.01


@pytest.mark.asyncio
async def test_bet_schema_fields(
    client: AsyncClient,
    match_with_stream: Match,
    fighters: tuple[Fighter, Fighter],
    cleanup,
):
    """Verify BetOut schema has all expected fields."""
    f1, _ = fighters
    resp = await client.post("/api/bets/", json={
        "match_id": str(match_with_stream.id),
        "fighter_id": str(f1.id),
        "amount": 1.0,
    })
    data = resp.json()
    expected_keys = {
        "id", "match_id", "fighter_id", "fighter_name", "opponent_name",
        "amount", "currency", "odds_at_placement", "status",
        "payout", "tx_signature", "placed_at", "settled_at",
    }
    assert expected_keys.issubset(set(data.keys())), f"Missing: {expected_keys - set(data.keys())}"
