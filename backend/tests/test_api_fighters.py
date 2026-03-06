"""Integration tests for fighters API."""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bet, BetStatus, Fighter, Match, MatchStatus, User


@pytest.mark.asyncio
async def test_list_fighters_empty(client: AsyncClient):
    """Test listing fighters when database is empty."""
    response = await client.get("/api/fighters/")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_fighters(client: AsyncClient, test_db: AsyncSession):
    """Test listing fighters."""
    # Create test fighters
    fighter1 = Fighter(
        name="Test Fighter 1",
        slug="test-fighter-1",
        character="Sub-Zero",
        character_id=0,
        llm_model="Test Model",
        agent_architecture="random",
    )
    fighter2 = Fighter(
        name="Test Fighter 2",
        slug="test-fighter-2",
        character="Scorpion",
        character_id=1,
        llm_model="Test Model",
        agent_architecture="cpu",
    )
    test_db.add(fighter1)
    test_db.add(fighter2)
    await test_db.commit()

    response = await client.get("/api/fighters/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Test Fighter 1"
    assert data[0]["agent_architecture"] == "random"


@pytest.mark.asyncio
async def test_get_fighter(client: AsyncClient, test_db: AsyncSession):
    """Test getting single fighter by ID."""
    fighter = Fighter(
        name="Test Fighter",
        slug="test-fighter",
        character="Sub-Zero",
        character_id=0,
        llm_model="Test Model",
        agent_architecture="random",
    )
    test_db.add(fighter)
    await test_db.commit()
    await test_db.refresh(fighter)

    response = await client.get(f"/api/fighters/{fighter.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Fighter"
    assert data["slug"] == "test-fighter"


@pytest.mark.asyncio
async def test_get_fighter_not_found(client: AsyncClient):
    """Test getting non-existent fighter returns 404."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/fighters/{fake_id}")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "FighterNotFoundError"


@pytest.mark.asyncio
async def test_fighter_stats_counts_claimed_bets_as_wins(
    client: AsyncClient,
    test_db: AsyncSession,
):
    """Fighter stats should treat CLAIMED winner bets as wins."""
    user = User(
        privy_user_id="stats-user",
        wallet_address="So1statswallet11111111111111111111111111111111111",
    )
    fighter1 = Fighter(
        name="Stats Fighter A",
        slug="stats-fighter-a",
        character="Sub-Zero",
        character_id=11,
        llm_model="test-model",
        agent_architecture="random",
    )
    fighter2 = Fighter(
        name="Stats Fighter B",
        slug="stats-fighter-b",
        character="Scorpion",
        character_id=12,
        llm_model="test-model",
        agent_architecture="cpu",
    )
    test_db.add_all([user, fighter1, fighter2])
    await test_db.flush()

    now = datetime.now(timezone.utc)
    match = Match(
        fighter1_id=fighter1.id,
        fighter2_id=fighter2.id,
        p1_agent="random",
        p2_agent="cpu",
        status=MatchStatus.COMPLETED,
        scheduled_at=now,
        completed_at=now,
        winner_id=fighter1.id,
    )
    test_db.add(match)
    await test_db.flush()

    bets = [
        Bet(
            user_id=user.id,
            match_id=match.id,
            fighter_id=fighter1.id,
            amount=1.0,
            odds_at_placement=2.0,
            status=BetStatus.WON,
        ),
        Bet(
            user_id=user.id,
            match_id=match.id,
            fighter_id=fighter1.id,
            amount=1.5,
            odds_at_placement=2.0,
            status=BetStatus.CLAIMED,
        ),
        Bet(
            user_id=user.id,
            match_id=match.id,
            fighter_id=fighter1.id,
            amount=1.0,
            odds_at_placement=2.0,
            status=BetStatus.LOST,
        ),
    ]
    test_db.add_all(bets)
    await test_db.commit()

    response = await client.get(f"/api/fighters/{fighter1.id}/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_bets_won"] == 2
