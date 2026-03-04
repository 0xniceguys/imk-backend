"""
Async Redis client — singleton with helper methods for match state caching.

Used by:
- match_runner.py: write game state + match_ended to cache as match runs
- ws/game_state.py: read cache on WS connect so late joiners get immediate state
- connection_manager.py: pub/sub fan-out for multi-server deployments
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

# TTLs
_STATE_TTL = 600   # 10 min — last game state per match
_ENDED_TTL = 300   # 5 min  — terminal match_ended event

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return the singleton async Redis client (connection-pooled)."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=False,   # we handle encoding ourselves
            encoding="utf-8",
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


# ── Cache helpers ────────────────────────────────────────────────────────────


async def cache_game_state(match_id: str, state: dict) -> None:
    """Cache the latest game state snapshot for a match."""
    try:
        r = get_redis()
        await r.set(f"match:{match_id}:last_state", json.dumps(state), ex=_STATE_TTL)
    except Exception:
        logger.debug("Redis cache_game_state failed for %s (non-fatal)", match_id)


async def get_cached_state(match_id: str) -> dict | None:
    """Return the last cached game state for a match, or None."""
    try:
        r = get_redis()
        raw = await r.get(f"match:{match_id}:last_state")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def cache_match_ended(match_id: str, payload: dict) -> None:
    """Cache the match_ended event so late joiners receive it on connect."""
    try:
        r = get_redis()
        await r.set(f"match:{match_id}:ended", json.dumps(payload), ex=_ENDED_TTL)
    except Exception:
        logger.debug("Redis cache_match_ended failed for %s (non-fatal)", match_id)


async def get_match_ended(match_id: str) -> dict | None:
    """Return the cached match_ended payload, or None if match is still live."""
    try:
        r = get_redis()
        raw = await r.get(f"match:{match_id}:ended")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def clear_match_cache(match_id: str) -> None:
    """Clear all cached data for a match (call at match start to avoid stale data)."""
    try:
        r = get_redis()
        await r.delete(
            f"match:{match_id}:last_state",
            f"match:{match_id}:ended",
        )
        logger.debug("Cleared Redis cache for match %s", match_id)
    except Exception:
        logger.debug("Redis clear_match_cache failed for %s (non-fatal)", match_id)


# ── Pub/Sub helpers ──────────────────────────────────────────────────────────


def events_channel(match_id: str) -> str:
    return f"match:{match_id}:events"


def frames_channel(match_id: str) -> str:
    return f"match:{match_id}:frames"


async def publish_json(match_id: str, data: dict) -> None:
    """Publish a JSON event to the match events channel (for multi-server fan-out)."""
    try:
        r = get_redis()
        await r.publish(events_channel(match_id), json.dumps(data))
    except Exception:
        logger.debug("Redis publish_json failed for %s (non-fatal)", match_id)


async def publish_bytes(match_id: str, data: bytes) -> None:
    """Publish binary frame bytes to the match frames channel."""
    try:
        r = get_redis()
        await r.publish(frames_channel(match_id), data)
    except Exception:
        logger.debug("Redis publish_bytes failed for %s (non-fatal)", match_id)
