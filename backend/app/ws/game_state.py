"""WebSocket endpoint for live match game state + frames."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.match_runner import get_runner
from app.ws.connection_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/match/{match_id}")
async def match_websocket(ws: WebSocket, match_id: str):
    """
    Connect to a live match stream.

    Receives:
    - JSON messages: {"type": "game_state", ...} with health, timer, positions
    - Binary messages: PNG frame bytes from the emulator

    Sends (client → server):
    - {"type": "chat", "message": "..."} for live chat (future)
    """
    runner = get_runner(match_id)
    if not runner:
        # Must accept() FIRST — closing before accept() produces HTTP 403
        # instead of a proper WebSocket close frame, causing client crash loops.
        await ws.accept()
        await ws.close(code=4004, reason="Match not live — no active runner")
        return

    await manager.connect(ws, match_id)

    # Send initial state immediately so the client doesn't start blank
    try:
        await ws.send_json({
            "type": "connected",
            "match_id": match_id,
            "viewer_count": manager.viewer_count(match_id),
            "game_state": runner.latest_snapshot.to_dict(),
        })
    except Exception:
        manager.disconnect(ws, match_id)
        return

    # Broadcast updated viewer count
    await manager.broadcast_json(match_id, {
        "type": "viewer_count",
        "count": manager.viewer_count(match_id),
    })

    try:
        while True:
            # Keep connection alive; handle incoming messages (chat, etc.)
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                # Broadcast chat message to all viewers
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
        manager.disconnect(ws, match_id)
        # Broadcast updated viewer count
        await manager.broadcast_json(match_id, {
            "type": "viewer_count",
            "count": manager.viewer_count(match_id),
        })
