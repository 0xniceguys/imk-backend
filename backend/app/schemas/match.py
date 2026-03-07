from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.fighter import FighterOut


class OddsOut(BaseModel):
    fighter1_odds: float
    fighter2_odds: float
    fighter1_pool_pct: float
    fighter2_pool_pct: float
    fighter1_pool: float = 0.0
    fighter2_pool: float = 0.0
    total_pool: float
    active_bets: int


class MatchBetFeedOut(BaseModel):
    wallet_masked: str
    side: str
    fighter_name: str
    amount: float
    status: str
    placed_at: datetime


class MatchOut(BaseModel):
    id: UUID
    fighter1: FighterOut | None = None
    fighter2: FighterOut | None = None
    status: str
    label: str
    scheduled_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    winner_id: UUID | None = None
    stream_url: str | None = None
    odds: OddsOut | None = None
    best_of: int = 3
    current_round: int = 1
    rounds_won_p1: int = 0
    rounds_won_p2: int = 0
    betting_open: bool = False
    queue_position: int | None = None
    queue_starts_at: datetime | None = None
    queue_countdown_seconds: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MatchCreate(BaseModel):
    fighter1_id: UUID
    fighter2_id: UUID
    scheduled_at: datetime
    label: str = "MK4-Classic"
    savestate_path: str | None = None
    p1_agent: str = "random"
    p2_agent: str = "random"
    best_of: int = 3
