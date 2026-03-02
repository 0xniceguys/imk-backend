from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class BetCreate(BaseModel):
    match_id: UUID
    fighter_id: UUID
    amount: float


class BetOut(BaseModel):
    id: UUID
    match_id: UUID
    fighter_id: UUID
    fighter_name: str = ""
    opponent_name: str = ""
    amount: float
    currency: str = "SOL"
    odds_at_placement: float
    status: str
    payout: float | None = None
    tx_signature: str | None = None
    placed_at: datetime
    settled_at: datetime | None = None

    model_config = {"from_attributes": True}
