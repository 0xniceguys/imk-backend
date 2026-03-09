import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ✅ Configure structured logging
from app.logging_config import configure_logging, get_logger

configure_logging(log_level="INFO")

from app.admin_views import router as admin_views_router
from app.api.router import api_router
from app.config import settings
from app.middleware import error_handler_middleware
from app.ws.game_state import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    import logging
    logger = logging.getLogger(__name__)
    queue_loop_started = False

    # Clean up any orphaned processes from previous runs
    from app.services.process_manager import full_cleanup
    stats = full_cleanup()
    logger.info(f"Startup cleanup: {stats}")

    # Clean up any stale LIVE matches from previous runs
    from app.db.engine import async_session
    from app.db.models import Match, MatchStatus
    from sqlalchemy import update
    from datetime import datetime, timezone

    async with async_session() as db:
        # Find matches that are LIVE but have no active runner
        # These are matches that were interrupted by a service restart
        result = await db.execute(
            update(Match)
            .where(Match.status == MatchStatus.LIVE)
            .values(
                status=MatchStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc),
                # Don't set a winner since we don't know who won
            )
            .returning(Match.id)
        )
        updated_ids = result.scalars().all()
        await db.commit()

        if updated_ids:
            logger.warning(f"Cleaned up {len(updated_ids)} stale LIVE matches: {updated_ids}")

    # Pre-fetch Privy JWKS so first auth request is fast
    from app.auth.privy import verify_privy_token  # noqa: F401

    if settings.auto_queue_enabled:
        from app.services.queue_loop import queue_loop_manager

        await queue_loop_manager.start()
        queue_loop_started = True
        logger.info("Auto queue loop enabled")
    else:
        logger.info("Auto queue loop disabled via config")

    yield

    # Shutdown: gracefully stop all running matches
    logger.info("Shutting down IMK backend...")
    if queue_loop_started:
        from app.services.queue_loop import queue_loop_manager

        await queue_loop_manager.stop()
    from app.services.match_runner import get_all_runners, stop_match

    runners = list(get_all_runners().keys())
    if runners:
        logger.info(f"Stopping {len(runners)} running matches...")
        for mid in runners:
            try:
                await stop_match(mid)
                logger.info(f"  ✓ Stopped match {mid}")
            except Exception as e:
                logger.error(f"  ✗ Error stopping match {mid}: {e}")

    # Final cleanup
    stats = full_cleanup()
    logger.info(f"Shutdown cleanup: {stats}")
    # Close Redis connection pool
    try:
        from app.services.redis_client import close_redis
        await close_redis()
        logger.info("Redis connection closed")
    except Exception:
        pass

    logger.info("IMK backend shutdown complete")


app = FastAPI(
    title="Immortal Kombat",
    version="0.1.0",
    lifespan=lifespan,
)

# ✅ Error handler middleware (must be added FIRST, before CORS)
app.middleware("http")(error_handler_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(admin_views_router)
app.include_router(ws_router)

# ✅ Serve uploaded files (fighter images, etc.)
UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


@app.get("/health")
async def health():
    """Basic health check for load balancer."""
    return {"status": "ok"}

# ── Combined H.264+AAC HLS Streaming ────────────────────────────────────────

from fastapi.responses import FileResponse, Response  # noqa: E402
from app.services.ffmpeg_combined_hls import hls_playlist_path, hls_dir  # noqa: E402


@app.get("/stream/{match_id}/stream.m3u8")
async def stream_playlist(match_id: str):
    """Serve the combined HLS playlist (video+audio) for a live match."""
    path = hls_playlist_path(match_id)
    if not path.exists():
        # Return an empty 404 body for media clients. JSON error payloads can be
        # misinterpreted by some HLS parsers as transport stream bytes.
        return Response(
            status_code=404,
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Cache-Control": "no-cache, no-store",
                "Access-Control-Allow-Origin": "*",
                "Retry-After": "1",
            },
        )
    return FileResponse(
        str(path),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache, no-store", "Access-Control-Allow-Origin": "*"},
    )


@app.get("/stream/{match_id}/{segment}")
async def stream_segment(match_id: str, segment: str):
    """Serve an individual HLS .ts segment."""
    if not segment.endswith(".ts"):
        return Response(status_code=400)
    path = hls_dir(match_id) / segment
    if not path.exists():
        # Empty 404 avoids sending JSON bodies to media decoders.
        return Response(
            status_code=404,
            media_type="video/mp2t",
            headers={"Cache-Control": "no-cache, no-store", "Access-Control-Allow-Origin": "*"},
        )
    return FileResponse(
        str(path),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"},
    )


# Legacy audio-only path — redirects to combined stream for backward compat
@app.get("/stream/audio/{match_id}/stream.m3u8")
async def audio_playlist_compat(match_id: str):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/stream/{match_id}/stream.m3u8")


@app.get("/stream/audio/{match_id}/{segment}")
async def audio_segment_compat(match_id: str, segment: str):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/stream/{match_id}/{segment}")


# ── WebRTC Signaling (when use_webrtc=True) ──────────────────────────────────

@app.post("/stream/{match_id}/webrtc/offer")
async def webrtc_offer(match_id: str, body: dict):
    """Flutter sends SDP offer; we forward to mediasoup and return SDP answer."""
    from fastapi.responses import JSONResponse  # noqa: E402
    import httpx as _httpx  # noqa: E402
    if not settings.use_webrtc:
        return JSONResponse(status_code=404, content={"error": "WebRTC not enabled"})
    sdp_offer = body.get("sdpOffer", "")
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.mediasoup_url}/rooms/{match_id}/consume",
                json={"sdpOffer": sdp_offer},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@app.get("/health/detailed")
async def health_detailed():
    """Detailed health check with DB, runners, and system status."""
    import psutil
    from app.db.engine import async_session
    from app.services.match_runner import get_all_runners
    from sqlalchemy import text

    health = {
        "status": "ok",
        "database": {"status": "unknown"},
        "runners": {"count": 0, "matches": []},
        "system": {
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
        },
    }

    # Check database
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        health["database"]["status"] = "connected"
    except Exception as e:
        health["status"] = "degraded"
        health["database"]["status"] = "error"
        health["database"]["error"] = str(e)

    # Check Redis
    health["redis"] = {"status": "unknown"}
    try:
        from app.services.redis_client import get_redis
        await get_redis().ping()
        health["redis"]["status"] = "connected"
    except Exception as e:
        health["redis"]["status"] = "unavailable"
        health["redis"]["error"] = str(e)

    # Check runners
    try:
        runners = get_all_runners()
        health["runners"]["count"] = len(runners)
        health["runners"]["matches"] = [
            {"match_id": mid, "status": r.status.value if hasattr(r, 'status') else "unknown"}
            for mid, r in list(runners.items())[:10]  # Limit to 10
        ]
    except Exception as e:
        health["runners"]["error"] = str(e)

    # System metrics
    try:
        health["system"]["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        health["system"]["memory_percent"] = psutil.virtual_memory().percent
    except Exception:
        pass

    return health
