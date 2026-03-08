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


@router.websocket("/ws/events")
async def global_events_websocket(ws: WebSocket):
    """
    Global events channel for all match status changes.
    Clients connect here to get notified when ANY match goes live.
    """
    await ws.accept()
    manager.subscribe_global(ws)
    client_id = f"client-{id(ws)}"

    try:
        # Send initial connection confirmation
        await ws.send_json({
            "type": "connected",
            "client_id": client_id,
        })

        # Keep connection alive and handle pings
        while True:
            try:
                data = await ws.receive_json()
                if data.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except Exception:
                break

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Global events WebSocket error")
    finally:
        manager.unsubscribe_global(ws)


@router.websocket("/ws/match/{match_id}")
async def match_websocket(ws: WebSocket, match_id: str):
    """
    Connect to a live match stream.

    On connect:
    1. If match already ended (Redis cache) → send match_ended immediately
    2. If runner is on ANOTHER worker (Redis has cached state) → admit client
       and rely on Redis pub/sub for event delivery
    3. Send current game state from cache so client isn't blank
    4. Subscribe to Redis pub/sub for cross-server frame/event fan-out
    """
    runner = get_runner(match_id)

    # ── Check Redis for terminal state before checking runner ──────────────
    ended_payload = await _get_match_ended(match_id)

    if not runner and not ended_payload:
        # Check Redis for a cached game_state — if present, the match IS live
        # on another Uvicorn worker. We must admit the client (Redis pub/sub
        # will deliver events from the runner worker to us).
        cached_state = await _get_cached_state(match_id)
        if not cached_state:
            # Genuinely no runner anywhere — match not started or never existed
            await ws.accept()
            await ws.close(code=4004, reason="Match not live — no active runner")
            return

        # Runner is on another worker — admit and rely on Redis pub/sub
        await manager.connect(ws, match_id)
        try:
            await ws.send_json({
                "type": "connected",
                "match_id": match_id,
                "viewer_count": manager.viewer_count(match_id),
                "runner_state": "running",
                "streaming_state": "ready",  # assume ready if we have cached state
                "match_ended": False,
                "game_state": cached_state,
            })
            # Emit streaming_state: ready so Flutter HLS preloader fires
            await ws.send_json({
                "type": "streaming_state",
                "state": "ready",
                "hls_url": f"/stream/{match_id}/stream.m3u8",
            })
        except Exception:
            manager.disconnect(ws, match_id)
            return

        await manager.broadcast_json(match_id, {
            "type": "viewer_count",
            "count": manager.viewer_count(match_id),
        })

        stop_event = asyncio.Event()
        redis_sub_task = asyncio.create_task(
            manager.start_redis_subscriber(match_id, ws, stop_event)
        )
        try:
            while True:
                data = await ws.receive_json()
                if data.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WebSocket error (cross-worker) for match %s", match_id)
        finally:
            stop_event.set()
            redis_sub_task.cancel()
            manager.disconnect(ws, match_id)
            await manager.broadcast_json(match_id, {
                "type": "viewer_count",
                "count": manager.viewer_count(match_id),
            })
        return

    if not runner and ended_payload:
        # Runner gone but we know the match ended — inform the client
        await ws.accept()
        await ws.send_json(ended_payload)
        await ws.close(code=1000, reason="Match has ended")
        return

    # Runner is alive on THIS worker ─────────────────────────────────────────
    await manager.connect(ws, match_id)

    is_ended = runner.state.value in ("completed", "stopped", "error")

    cached_state = await _get_cached_state(match_id) or runner.latest_snapshot.to_dict()

    try:
        await ws.send_json({
            "type": "connected",
            "match_id": match_id,
            "viewer_count": manager.viewer_count(match_id),
            "runner_state": runner.state.value,
            "streaming_state": runner.streaming_state.value,
            "match_ended": is_ended,
            "game_state": cached_state,
        })
        # Send current streaming state separately so Flutter's stream listener picks it up
        if runner.streaming_state.value in ("ready", "playing"):
            await ws.send_json({
                "type": "streaming_state",
                "state": runner.streaming_state.value,
                "hls_url": f"/stream/{match_id}/stream.m3u8",
            })
        elif runner.streaming_state.value != "not_started":
            await ws.send_json({
                "type": "streaming_state",
                "state": runner.streaming_state.value,
            })
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
