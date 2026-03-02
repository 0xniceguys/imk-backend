from fastapi import APIRouter

from app.api.auth import router as auth_router
from app.api.fighters import router as fighters_router
from app.api.matches import router as matches_router
from app.api.bets import router as bets_router
from app.api.admin import router as admin_router
from app.api.stream import router as stream_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(fighters_router)
api_router.include_router(matches_router)
api_router.include_router(bets_router)
api_router.include_router(admin_router)
api_router.include_router(stream_router)
