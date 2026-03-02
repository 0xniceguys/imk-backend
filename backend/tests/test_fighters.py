"""Test /api/fighters endpoints."""

import pytest
from httpx import AsyncClient

from app.db.models import Fighter


@pytest.mark.asyncio
async def test_list_fighters_empty(client: AsyncClient):
    """GET /api/fighters/ returns list (may include existing data)."""
    resp = await client.get("/api/fighters/")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_fighters_has_created(
    client: AsyncClient, fighters: tuple[Fighter, Fighter], cleanup
):
    """Created fighters appear in the list."""
    f1, f2 = fighters
    resp = await client.get("/api/fighters/")
    assert resp.status_code == 200
    data = resp.json()
    ids = [f["id"] for f in data]
    assert str(f1.id) in ids
    assert str(f2.id) in ids


@pytest.mark.asyncio
async def test_get_fighter_by_id(
    client: AsyncClient, fighters: tuple[Fighter, Fighter], cleanup
):
    """GET /api/fighters/{id} returns the correct fighter."""
    f1, _ = fighters
    resp = await client.get(f"/api/fighters/{f1.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == f1.name
    assert data["slug"] == f1.slug
    assert data["character"] == "MK4"
    assert "win_rate" in data
    assert data["win_rate"] == 0.0


@pytest.mark.asyncio
async def test_get_fighter_not_found(client: AsyncClient):
    """GET /api/fighters/{bad_id} returns 404."""
    resp = await client.get("/api/fighters/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fighter_schema_fields(
    client: AsyncClient, fighters: tuple[Fighter, Fighter], cleanup
):
    """Verify FighterOut schema has all expected fields."""
    f1, _ = fighters
    resp = await client.get(f"/api/fighters/{f1.id}")
    data = resp.json()
    expected_keys = {
        "id", "name", "slug", "character", "character_id",
        "llm_model", "image_url", "agent_architecture",
        "matches_played", "matches_won", "win_rate", "created_at",
    }
    assert expected_keys.issubset(set(data.keys())), f"Missing: {expected_keys - set(data.keys())}"
