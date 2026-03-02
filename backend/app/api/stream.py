"""HTTP endpoints for match streaming (frame polling fallback + status)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.services.match_runner import get_all_runners, get_runner
from app.ws.connection_manager import manager as ws_manager

router = APIRouter(prefix="/stream", tags=["stream"])


@router.get("/live")
async def list_live_matches():
    """List all currently running matches."""
    runners = get_all_runners()
    return [
        {
            "match_id": mid,
            "state": r.state.value,
            "viewer_count": ws_manager.viewer_count(mid),
            "frame_id": r.latest_snapshot.frame_id,
            "p1_health": r.latest_snapshot.p1_health,
            "p2_health": r.latest_snapshot.p2_health,
        }
        for mid, r in runners.items()
    ]


@router.get("/{match_id}/frame")
async def get_latest_frame(match_id: str):
    """Get the latest frame as a PNG image (polling fallback)."""
    runner = get_runner(match_id)
    if not runner:
        raise HTTPException(404, "Match not running")

    if not runner.latest_frame:
        raise HTTPException(503, "No frame captured yet")

    return Response(
        content=runner.latest_frame,
        media_type="image/jpeg",
    )


@router.get("/{match_id}/state")
async def get_game_state(match_id: str):
    """Get latest game state (polling fallback)."""
    runner = get_runner(match_id)
    if not runner:
        raise HTTPException(404, "Match not running")

    return runner.latest_snapshot.to_dict()
