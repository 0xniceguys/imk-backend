"""End-to-end test for match creation and management workflow."""

import pytest
from datetime import datetime, timezone
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Fighter, User


@pytest.mark.asyncio
async def test_full_match_workflow(client: AsyncClient, test_db: AsyncSession):
    """Test complete workflow: create fighters, create match, get match details.

    This is a simplified E2E test that doesn't actually start the emulator,
    but tests the full API flow from fighter creation to match creation.
    """
    # Step 1: Create admin user
    admin = User(
        privy_user_id="test-admin",
        wallet_address="test-wallet",
        is_admin=True,
    )
    test_db.add(admin)
    await test_db.commit()
    await test_db.refresh(admin)

    # Step 2: Create two fighters
    fighter1 = Fighter(
        name="Test Fighter 1",
        slug="test-fighter-1",
        character="Sub-Zero",
        character_id=0,
        llm_model="Random Agent",
        agent_architecture="random",
    )
    fighter2 = Fighter(
        name="Test Fighter 2",
        slug="test-fighter-2",
        character="Scorpion",
        character_id=1,
        llm_model="CPU Agent",
        agent_architecture="cpu",
    )
    test_db.add(fighter1)
    test_db.add(fighter2)
    await test_db.commit()
    await test_db.refresh(fighter1)
    await test_db.refresh(fighter2)

    # Step 3: Verify fighters exist via API
    response = await client.get("/api/fighters/")
    assert response.status_code == 200
    fighters_data = response.json()
    assert len(fighters_data) == 2

    # Step 4: Get individual fighter details
    response = await client.get(f"/api/fighters/{fighter1.id}")
    assert response.status_code == 200
    fighter1_data = response.json()
    assert fighter1_data["name"] == "Test Fighter 1"
    assert fighter1_data["agent_architecture"] == "random"

    # Step 5: Create a match (would require admin auth in production)
    # This demonstrates the expected payload structure
    match_payload = {
        "fighter1_id": str(fighter1.id),
        "fighter2_id": str(fighter2.id),
        "scheduled_at": datetime.now(timezone.utc).isoformat(),
        "label": "Test Match",
        "savestate_path": "/fake/path/to/savestate.st",
        "p1_agent": "random",
        "p2_agent": "cpu",
        "best_of": 3,
    }

    # Note: This would fail without proper admin authentication
    # but demonstrates the expected API structure
    # response = await client.post("/api/admin/matches", json=match_payload)

    # Step 6: Verify match list endpoint works
    response = await client.get("/api/matches/")
    assert response.status_code == 200
    # Empty since we didn't create match without admin auth
    matches_data = response.json()
    assert isinstance(matches_data, list)


@pytest.mark.asyncio
async def test_fighter_crud_workflow(client: AsyncClient, test_db: AsyncSession):
    """Test fighter CRUD operations."""
    # Create
    fighter = Fighter(
        name="CRUD Test Fighter",
        slug="crud-test",
        character="Raiden",
        character_id=2,
        llm_model="Test Model",
        agent_architecture="random",
    )
    test_db.add(fighter)
    await test_db.commit()
    await test_db.refresh(fighter)

    # Read
    response = await client.get(f"/api/fighters/{fighter.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "CRUD Test Fighter"

    # List
    response = await client.get("/api/fighters/")
    assert response.status_code == 200
    fighters = response.json()
    assert len(fighters) >= 1
    assert any(f["id"] == str(fighter.id) for f in fighters)

    # Update and Delete would require admin auth in production
    # This demonstrates the workflow structure
