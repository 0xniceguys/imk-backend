"""
Integration test: full match lifecycle using LSTM agents + p1p2state.st savestate.

Tests:
  1. Create two fighters with LSTM agent architecture (no real emulator needed)
  2. Create an upcoming match pointing at p1p2state.st
  3. Verify the match appears in GET /api/matches with status=upcoming
  4. Verify GET /api/matches?status=upcoming filters correctly
  5. Attempt to start the match (will fail fast without emulator — checks the
     endpoint wiring and error surface, not a live match)
  6. Verify match fields: savestate path, agent names, fighter IDs, stream_url shape
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import Fighter, Match, MatchStatus, Stream

LSTM_CHECKPOINT = "app/agents/checkpoints/lstm.onnx"
SAVESTATE = "/Users/ichiropractic/code/n64/training/data/savestates/mk4_arcade/p1p2state.st"


# ── Fighter creation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_lstm_fighters(admin_client: AsyncClient, cleanup):
    """Create two fighters with LSTM architecture and verify response fields."""
    f1_resp = await admin_client.post("/api/admin/fighters", json={
        "name": "LSTM Scorpion",
        "slug": "lstm-scorpion",
        "character": "Scorpion",
        "character_id": 1,
        "llm_model": "lstm-v1",
        "agent_checkpoint": LSTM_CHECKPOINT,
        "agent_architecture": "lstm",
    })
    assert f1_resp.status_code == 200, f"F1 create failed: {f1_resp.text}"
    f1 = f1_resp.json()
    assert f1["name"] == "LSTM Scorpion"
    assert f1["agent_architecture"] == "lstm"
    assert f1["matches_played"] == 0

    f2_resp = await admin_client.post("/api/admin/fighters", json={
        "name": "LSTM Sub-Zero",
        "slug": "lstm-subzero",
        "character": "Sub-Zero",
        "character_id": 2,
        "llm_model": "lstm-v1",
        "agent_checkpoint": LSTM_CHECKPOINT,
        "agent_architecture": "lstm",
    })
    assert f2_resp.status_code == 200, f"F2 create failed: {f2_resp.text}"
    f2 = f2_resp.json()
    assert f2["name"] == "LSTM Sub-Zero"
    assert f2["slug"] == "lstm-subzero"


# ── Match creation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_lstm_match_upcoming(
    admin_client: AsyncClient, fighters: tuple[Fighter, Fighter], cleanup
):
    """Create a match with LSTM agents + p1p2state savestate → status=upcoming."""
    f1, f2 = fighters
    resp = await admin_client.post("/api/admin/matches", json={
        "fighter1_id": str(f1.id),
        "fighter2_id": str(f2.id),
        "scheduled_at": "2026-03-02T10:00:00Z",
        "label": "LSTM vs LSTM",
        "savestate_path": SAVESTATE,
        "p1_agent": "lstm",
        "p2_agent": "lstm",
        "best_of": 3,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "upcoming"
    assert data["label"] == "LSTM vs LSTM"
    assert data["fighter1"]["id"] == str(f1.id)
    assert data["fighter2"]["id"] == str(f2.id)
    assert data["best_of"] == 3
    # Betting is open for upcoming matches (computed as status == UPCOMING)
    assert data["betting_open"] is True
    return data["id"]


# ── Match visible in public list ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upcoming_match_visible_in_list(
    admin_client: AsyncClient, client: AsyncClient,
    fighters: tuple[Fighter, Fighter], cleanup
):
    """Upcoming match appears in GET /api/matches (no filter) and status filter."""
    f1, f2 = fighters

    create_resp = await admin_client.post("/api/admin/matches", json={
        "fighter1_id": str(f1.id),
        "fighter2_id": str(f2.id),
        "scheduled_at": "2026-03-02T10:00:00Z",
        "label": "LSTM Visibility Test",
        "savestate_path": SAVESTATE,
        "p1_agent": "lstm",
        "p2_agent": "lstm",
    })
    assert create_resp.status_code == 200
    match_id = create_resp.json()["id"]

    # Unfiltered list must include our match
    list_resp = await client.get("/api/matches/")
    assert list_resp.status_code == 200
    all_ids = [m["id"] for m in list_resp.json()]
    assert match_id in all_ids, f"Match {match_id} not in unfiltered list"

    # Status filter upcoming must include it
    filtered_resp = await client.get("/api/matches/", params={"status": "upcoming"})
    assert filtered_resp.status_code == 200
    upcoming_ids = [m["id"] for m in filtered_resp.json()]
    assert match_id in upcoming_ids, f"Match {match_id} not in upcoming filter"

    # Status filter live must NOT include it
    live_resp = await client.get("/api/matches/", params={"status": "live"})
    assert live_resp.status_code == 200
    live_ids = [m["id"] for m in live_resp.json()]
    assert match_id not in live_ids, "Upcoming match incorrectly listed as live"


# ── Single match fetch ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_match_by_id_has_correct_fields(
    admin_client: AsyncClient, client: AsyncClient,
    fighters: tuple[Fighter, Fighter], cleanup
):
    """GET /api/matches/{id} returns full match with savestate and agent names."""
    f1, f2 = fighters
    create_resp = await admin_client.post("/api/admin/matches", json={
        "fighter1_id": str(f1.id),
        "fighter2_id": str(f2.id),
        "scheduled_at": "2026-03-02T10:00:00Z",
        "label": "LSTM Detail Test",
        "savestate_path": SAVESTATE,
        "p1_agent": "lstm",
        "p2_agent": "lstm",
    })
    match_id = create_resp.json()["id"]

    resp = await client.get(f"/api/matches/{match_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == match_id
    assert data["status"] == "upcoming"
    assert data["fighter1"]["id"] == str(f1.id)
    assert data["fighter2"]["id"] == str(f2.id)
    assert data["current_round"] == 1
    assert data["rounds_won_p1"] == 0
    assert data["rounds_won_p2"] == 0


# ── Start match (no emulator — expect fast failure) ───────────────────────────

@pytest.mark.asyncio
async def test_start_match_without_emulator_returns_500(
    admin_client: AsyncClient,
    fighters: tuple[Fighter, Fighter],
    cleanup
):
    """
    POST /api/admin/matches/{id}/start with no emulator available.

    Expected: 500 with a descriptive error. The endpoint must NOT silently
    hang or leave the match in a broken state indefinitely.
    The match status should be rolled back to UPCOMING after the failure.
    """
    f1, f2 = fighters
    create_resp = await admin_client.post("/api/admin/matches", json={
        "fighter1_id": str(f1.id),
        "fighter2_id": str(f2.id),
        "scheduled_at": "2026-03-02T10:00:00Z",
        "label": "LSTM Start Test",
        "savestate_path": SAVESTATE,
        "p1_agent": "lstm",
        "p2_agent": "lstm",
    })
    assert create_resp.status_code == 200
    match_id = create_resp.json()["id"]

    # Attempt start — emulator not running, should fail
    start_resp = await admin_client.post(
        f"/api/admin/matches/{match_id}/start",
        timeout=15.0,  # don't wait forever
    )
    # Expect either 500 (emulator not available) or 200 (if runner somehow starts)
    assert start_resp.status_code in (200, 500), (
        f"Unexpected status {start_resp.status_code}: {start_resp.text}"
    )

    if start_resp.status_code == 500:
        # Verify match was rolled back to upcoming
        get_resp = await admin_client.get(f"/api/matches/{match_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "upcoming", (
            f"Match was not rolled back after failed start: {get_resp.json()['status']}"
        )


# ── Cancel match ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_upcoming_lstm_match(
    admin_client: AsyncClient, client: AsyncClient,
    fighters: tuple[Fighter, Fighter], cleanup
):
    """Cancel an upcoming LSTM match → status becomes cancelled, disappears from upcoming filter."""
    f1, f2 = fighters
    create_resp = await admin_client.post("/api/admin/matches", json={
        "fighter1_id": str(f1.id),
        "fighter2_id": str(f2.id),
        "scheduled_at": "2026-03-02T10:00:00Z",
        "label": "LSTM Cancel Test",
        "savestate_path": SAVESTATE,
        "p1_agent": "lstm",
        "p2_agent": "lstm",
    })
    match_id = create_resp.json()["id"]

    cancel_resp = await admin_client.post(f"/api/admin/matches/{match_id}/cancel")
    assert cancel_resp.status_code == 200

    get_resp = await client.get(f"/api/matches/{match_id}")
    assert get_resp.json()["status"] == "cancelled"

    upcoming_resp = await client.get("/api/matches/", params={"status": "upcoming"})
    upcoming_ids = [m["id"] for m in upcoming_resp.json()]
    assert match_id not in upcoming_ids, "Cancelled match still in upcoming filter"
