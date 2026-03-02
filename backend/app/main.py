import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── Logging: stdout + rotating backend.log ──
_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)

_fmt = logging.Formatter(
    "%(asctime)s %(name)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_console = logging.StreamHandler()
_console.setFormatter(_fmt)

_file = logging.handlers.RotatingFileHandler(
    _log_dir / "backend.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
)
_file.setFormatter(_fmt)

logging.root.setLevel(logging.INFO)
logging.root.addHandler(_console)
logging.root.addHandler(_file)

from app.admin_views import router as admin_views_router
from app.api.router import api_router
from app.config import settings
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


@app.get("/health")
async def health():
    return {"status": "ok"}
