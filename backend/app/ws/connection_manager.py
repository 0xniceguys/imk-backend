"""WebSocket connection manager with room-based pub/sub."""

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
        """Send JSON payload to all viewers in a match room."""
        room = self._rooms.get(match_id)
        if not room:
            return
        message = json.dumps(data)
        dead: list[WebSocket] = []
        tasks = []
        for ws in room:
            tasks.append(self._safe_send(ws, message, dead))
        await asyncio.gather(*tasks)
        for ws in dead:
            room.discard(ws)

    async def broadcast_bytes(self, match_id: str, data: bytes) -> None:
        """Send binary payload (e.g. frame PNG) to all viewers in a match room."""
        room = self._rooms.get(match_id)
        if not room:
            return
        dead: list[WebSocket] = []
        tasks = []
        for ws in room:
            tasks.append(self._safe_send_bytes(ws, data, dead))
        await asyncio.gather(*tasks)
        for ws in dead:
            room.discard(ws)

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


# Singleton — shared across the app
manager = ConnectionManager()
