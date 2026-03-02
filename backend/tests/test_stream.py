"""Test /api/stream endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_live_matches_empty(client: AsyncClient):
    """GET /api/stream/live returns empty list when no matches running."""
    resp = await client.get("/api/stream/live")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_frame_no_runner(client: AsyncClient):
    """GET /api/stream/{id}/frame returns 404 when match not running."""
    resp = await client.get("/api/stream/fake-match-id/frame")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_state_no_runner(client: AsyncClient):
    """GET /api/stream/{id}/state returns 404 when match not running."""
    resp = await client.get("/api/stream/fake-match-id/state")
    assert resp.status_code == 404
