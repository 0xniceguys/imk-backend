"""WebSocket connection manager with room-based pub/sub.

Binary frame protocol (1-byte type prefix):
  0x00  = video JPEG frame
  0x01  = audio Opus/OGG chunk

Architecture:
- In-process broadcast: immediate delivery to clients on THIS server instance
- Redis pub/sub: fan-out to clients on OTHER server instances (multi-server scaling)

Both paths operate independently. Redis failures never break local delivery.

Broadcast strategy: fire-and-forget per client with a 100ms per-send timeout.
A slow or dead client never blocks the emulator agent loop or other clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Audio message prefix byte (video is sent raw — JPEG always starts with 0xFF)
FRAME_AUDIO: bytes = b"\x01"

# Per-client send timeout: if a client can't receive within this window,
# it is marked dead and removed from the room.
_SEND_TIMEOUT = 0.1  # seconds


class ConnectionManager:
    """Manages WebSocket connections grouped by match rooms."""

    def __init__(self) -> None:
        # match_id (str) → set of active WebSocket connections
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
        # Global events subscribers (for match status changes)
        self._global_subscribers: set[WebSocket] = set()
        # Connections queued for removal (pruned on next broadcast)
        self._dead: set[WebSocket] = set()
        # Background task: periodically prune dead connections even if no
        # broadcasts happen (e.g., between rounds when no frames are sent)
        self._prune_task: asyncio.Task | None = None

    def _start_prune_task(self) -> None:
        if self._prune_task is None or self._prune_task.done():
            self._prune_task = asyncio.create_task(
                self._periodic_prune(), name="ws-prune"
            )

    async def _periodic_prune(self) -> None:
        """Every 60s, sweep all rooms and drop any closed connections."""
        while True:
            await asyncio.sleep(60)
            for match_id in list(self._rooms.keys()):
                self._prune_dead(match_id)

    async def connect(self, ws: WebSocket, match_id: str) -> None:
        await ws.accept()
        self._rooms[match_id].add(ws)
        self._start_prune_task()  # ensure periodic pruner is running
        logger.info(
            "WS connected to match %s (%d viewers)",
            match_id, len(self._rooms[match_id]),
        )

    def disconnect(self, ws: WebSocket, match_id: str) -> None:
        self._rooms[match_id].discard(ws)
        if not self._rooms[match_id]:
            del self._rooms[match_id]

    def subscribe_global(self, ws: WebSocket) -> None:
        """Subscribe to global events (match status changes)."""
        self._global_subscribers.add(ws)
        logger.info("Global subscriber added (%d total)", len(self._global_subscribers))

    def unsubscribe_global(self, ws: WebSocket) -> None:
        """Unsubscribe from global events."""
        self._global_subscribers.discard(ws)
        logger.info("Global subscriber removed (%d total)", len(self._global_subscribers))

    def viewer_count(self, match_id: str) -> int:
        return len(self._rooms.get(match_id, set()))

    # ── Internal helpers ────────────────────────────────────────────────────

    def _prune_dead(self, match_id: str) -> None:
        """Remove any dead connections from the room."""
        room = self._rooms.get(match_id)
        if room and self._dead:
            room -= self._dead
            self._dead.clear()
            if not room:
                del self._rooms[match_id]

    async def _fire_text(self, ws: WebSocket, message: str) -> None:
        """Send a text frame to one client; mark dead on any failure."""
        try:
            await asyncio.wait_for(ws.send_text(message), timeout=_SEND_TIMEOUT)
        except Exception:
            self._dead.add(ws)

    async def _fire_bytes(self, ws: WebSocket, data: bytes) -> None:
        """Send a binary frame to one client; mark dead on any failure."""
        try:
            await asyncio.wait_for(ws.send_bytes(data), timeout=_SEND_TIMEOUT)
        except Exception:
            self._dead.add(ws)

    def _schedule_bytes(self, match_id: str, data: bytes) -> None:
        """Fire-and-forget binary broadcast to all local clients."""
        room = self._rooms.get(match_id)
        if not room:
            return
        self._prune_dead(match_id)
        for ws in list(room):  # snapshot to avoid mutation during iteration
            asyncio.create_task(
                self._fire_bytes(ws, data),
                name=f"ws-send-bytes-{match_id}",
            )

    def _schedule_text(self, match_id: str, message: str) -> None:
        """Fire-and-forget text broadcast to all local clients."""
        room = self._rooms.get(match_id)
        if not room:
            return
        self._prune_dead(match_id)
        for ws in list(room):
            asyncio.create_task(
                self._fire_text(ws, message),
                name=f"ws-send-text-{match_id}",
            )

    # ── Public broadcast API ────────────────────────────────────────────────

    async def broadcast_global_event(self, event: dict) -> None:
        """Broadcast an event to all global subscribers."""
        message = json.dumps(event)
        dead_subs = set()

        for ws in list(self._global_subscribers):
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=_SEND_TIMEOUT)
            except Exception:
                dead_subs.add(ws)

        # Clean up dead connections
        self._global_subscribers -= dead_subs

        # Also publish to Redis for multi-server setups
        try:
            from app.services.redis_client import publish_global_event
            await publish_global_event(event)
        except Exception:
            pass

    async def broadcast_json(self, match_id: str, data: dict) -> None:
        """Send JSON payload to all local viewers + publish to Redis (fire-and-forget)."""
        message = json.dumps(data)

        # 1. In-process (fire-and-forget)
        self._schedule_text(match_id, message)

        # 2. Redis fan-out — fully fire-and-forget: Redis slowness must never
        #    delay game state delivery to local clients.
        asyncio.create_task(
            self._redis_publish_json(match_id, data),
            name=f"redis-pub-json-{match_id}",
        )

    async def _redis_publish_json(self, match_id: str, data: dict) -> None:
        try:
            from app.services.redis_client import publish_json
            await publish_json(match_id, data)
        except Exception:
            pass

    async def broadcast_bytes(self, match_id: str, jpeg_bytes: bytes) -> None:
        """Send a raw video JPEG frame to all local viewers + Redis (fire-and-forget)."""
        # 1. In-process (fire-and-forget)
        self._schedule_bytes(match_id, jpeg_bytes)

        # 2. Redis fan-out — fire-and-forget
        asyncio.create_task(
            self._redis_publish_bytes(match_id, jpeg_bytes),
            name=f"redis-pub-bytes-{match_id}",
        )

    async def broadcast_audio(self, match_id: str, opus_bytes: bytes) -> None:
        """Send an audio Opus/OGG chunk (prefixed with 0x01) to all local viewers + Redis."""
        data = FRAME_AUDIO + opus_bytes

        # 1. In-process (fire-and-forget)
        self._schedule_bytes(match_id, data)

        # 2. Redis fan-out — fire-and-forget
        asyncio.create_task(
            self._redis_publish_bytes(match_id, data),
            name=f"redis-pub-audio-{match_id}",
        )

    async def _redis_publish_bytes(self, match_id: str, data: bytes) -> None:
        try:
            from app.services.redis_client import publish_bytes
            await publish_bytes(match_id, data)
        except Exception:
            pass

    async def start_redis_subscriber(
        self, match_id: str, ws: WebSocket, stop_event: asyncio.Event
    ) -> None:
        """Subscribe to Redis pub/sub channels for this match and forward messages
        to the given WebSocket. Runs until stop_event is set or WS disconnects.

        This enables clients on OTHER server instances to receive frames + events
        published by the match runner on the primary server.

        Note: binary frames already include the 0x00/0x01 type-prefix byte because
        broadcast_bytes / broadcast_audio add it before publishing to Redis.
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
                            # data already has 0x00/0x01 prefix
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
            logger.debug(
                "Redis subscriber unavailable for match %s (non-fatal)", match_id
            )


# Singleton — shared across the app
manager = ConnectionManager()
