"""WebSocket connection manager with room-based pub/sub.

Architecture:
- In-process broadcast: immediate delivery to clients on THIS server instance
- Redis pub/sub: fan-out to clients on OTHER server instances (multi-server scaling)

Both paths operate independently. Redis failures never break local delivery.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections grouped by match rooms."""

    def __init__(self) -> None:
        # match_id (str) → set of active WebSocket connections
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, ws: WebSocket, match_id: str) -> None:
        await ws.accept()
        self._rooms[match_id].add(ws)
        logger.info("WS connected to match %s (%d viewers)", match_id, len(self._rooms[match_id]))

    def disconnect(self, ws: WebSocket, match_id: str) -> None:
        self._rooms[match_id].discard(ws)
        if not self._rooms[match_id]:
            del self._rooms[match_id]

    def viewer_count(self, match_id: str) -> int:
        return len(self._rooms.get(match_id, set()))

    async def broadcast_json(self, match_id: str, data: dict) -> None:
        """Send JSON payload to all local viewers + publish to Redis for remote servers."""
        # 1. In-process delivery (fast path)
        room = self._rooms.get(match_id)
        if room:
            message = json.dumps(data)
            dead: list[WebSocket] = []
            tasks = [self._safe_send(ws, message, dead) for ws in room]
            await asyncio.gather(*tasks)
            for ws in dead:
                room.discard(ws)

        # 2. Redis fan-out (non-blocking, non-fatal)
        try:
            from app.services.redis_client import publish_json
            await publish_json(match_id, data)
        except Exception:
            pass

    async def broadcast_bytes(self, match_id: str, data: bytes) -> None:
        """Send binary payload (JPEG frame) to all local viewers + Redis."""
        # 1. In-process delivery
        room = self._rooms.get(match_id)
        if room:
            dead: list[WebSocket] = []
            tasks = [self._safe_send_bytes(ws, data, dead) for ws in room]
            await asyncio.gather(*tasks)
            for ws in dead:
                room.discard(ws)

        # 2. Redis fan-out
        try:
            from app.services.redis_client import publish_bytes
            await publish_bytes(match_id, data)
        except Exception:
            pass

    @staticmethod
    async def _safe_send(ws: WebSocket, message: str, dead: list[WebSocket]) -> None:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)

    @staticmethod
    async def _safe_send_bytes(ws: WebSocket, data: bytes, dead: list[WebSocket]) -> None:
        try:
            await ws.send_bytes(data)
        except Exception:
            dead.append(ws)

    async def start_redis_subscriber(
        self, match_id: str, ws: WebSocket, stop_event: asyncio.Event
    ) -> None:
        """Subscribe to Redis pub/sub channels for this match and forward messages
        to the given WebSocket. Runs until stop_event is set or WS disconnects.

        This enables clients on OTHER server instances to receive frames + events
        published by the match runner on the primary server.
        """
        try:
            from app.services.redis_client import (
                get_redis,
                events_channel,
                frames_channel,
            )
            r = get_redis()
            pubsub = r.pubsub()
            await pubsub.subscribe(events_channel(match_id), frames_channel(match_id))
            logger.debug("Redis subscriber started for match %s", match_id)

            async def _listen() -> None:
                async for msg in pubsub.listen():
                    if stop_event.is_set():
                        break
                    if msg["type"] not in ("message", "pmessage"):
                        continue
                    data = msg["data"]
                    channel = msg["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode()
                    try:
                        if f":{match_id}:frames" in channel and isinstance(data, bytes):
                            await ws.send_bytes(data)
                        elif f":{match_id}:events" in channel:
                            text = data.decode() if isinstance(data, bytes) else data
                            await ws.send_text(text)
                    except Exception:
                        break  # WS disconnected

            listen_task = asyncio.create_task(_listen())
            await stop_event.wait()
            listen_task.cancel()
            await pubsub.unsubscribe()
            await pubsub.aclose()
            logger.debug("Redis subscriber stopped for match %s", match_id)
        except Exception:
            logger.debug("Redis subscriber unavailable for match %s (non-fatal)", match_id)


# Singleton — shared across the app
manager = ConnectionManager()
