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

    # Clean up any orphaned processes from previous runs
    from app.services.process_manager import full_cleanup
    stats = full_cleanup()
    logger.info(f"Startup cleanup: {stats}")

    # Pre-fetch Privy JWKS so first auth request is fast
    from app.auth.privy import verify_privy_token  # noqa: F401

    yield

    # Shutdown: gracefully stop all running matches
    logger.info("Shutting down IMK backend...")
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
