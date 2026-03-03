"""Integration tests for fighters API."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Fighter


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
