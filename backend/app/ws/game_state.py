"""WebSocket endpoint for live match game state + frames."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.match_runner import get_runner
from app.ws.connection_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_cached_state(match_id: str) -> dict | None:
    try:
        from app.services.redis_client import get_cached_state
    except Exception:
        return None
    try:
        return await get_cached_state(match_id)
    except Exception:
        return None


async def _get_match_ended(match_id: str) -> dict | None:
    try:
        from app.services.redis_client import get_match_ended
    except Exception:
        return None
    try:
        return await get_match_ended(match_id)
    except Exception:
        return None


@router.websocket("/ws/match/{match_id}")
async def match_websocket(ws: WebSocket, match_id: str):
    """
    Connect to a live match stream.

    On connect:
    1. If match already ended (Redis cache) → send match_ended immediately
    2. Send current game state from cache so client isn't blank
    3. Subscribe to Redis pub/sub for cross-server frame/event fan-out
    """
    runner = get_runner(match_id)

    # ── Check Redis for terminal state before checking runner ──────────────
    # Handles: client connects after match ended AND runner was cleaned up.
    # We need to accept() before close() to avoid HTTP 403 instead of WS close.
    ended_payload = await _get_match_ended(match_id)

    if not runner and not ended_payload:
        # No runner and no cached end — match not started or never existed
        await ws.accept()
        await ws.close(code=4004, reason="Match not live — no active runner")
        return

    if not runner and ended_payload:
        # Runner gone but we know the match ended — inform the client
        await ws.accept()
        await ws.send_json(ended_payload)
        await ws.close(code=1000, reason="Match has ended")
        return

    # Runner is alive ─────────────────────────────────────────────────────
    await manager.connect(ws, match_id)

    # Determine if runner is in a terminal state
    is_ended = runner.state.value in ("completed", "stopped", "error")

    # Build initial connected message with cached or live state
    cached_state = await _get_cached_state(match_id) or runner.latest_snapshot.to_dict()

    try:
        await ws.send_json({
            "type": "connected",
            "match_id": match_id,
            "viewer_count": manager.viewer_count(match_id),
            "runner_state": runner.state.value,
            "match_ended": is_ended,
            "game_state": cached_state,
        })
        # Re-emit match_ended so Flutter's listener fires for late joiners
        if is_ended:
            payload = {"type": "match_ended", "match_id": match_id, "runner_state": runner.state.value}
            await ws.send_json(payload)
    except Exception:
        manager.disconnect(ws, match_id)
        return

    # Broadcast updated viewer count
    await manager.broadcast_json(match_id, {
        "type": "viewer_count",
        "count": manager.viewer_count(match_id),
    })

    # ── Start Redis subscriber for cross-server fan-out ───────────────────
    stop_event = asyncio.Event()
    redis_sub_task = asyncio.create_task(
        manager.start_redis_subscriber(match_id, ws, stop_event)
    )

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                await manager.broadcast_json(match_id, {
                    "type": "chat",
                    "user": data.get("user", "anon"),
                    "message": data.get("message", ""),
                })
            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error for match %s", match_id)
    finally:
        stop_event.set()
        redis_sub_task.cancel()
        manager.disconnect(ws, match_id)
        await manager.broadcast_json(match_id, {
            "type": "viewer_count",
            "count": manager.viewer_count(match_id),
        })
