from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, computed_field


class FighterOut(BaseModel):
    id: UUID
    name: str
    slug: str
    character: str
    character_id: int
    llm_model: str
    image_url: str | None = None
    agent_architecture: str | None = None
    matches_played: int = 0
    matches_won: int = 0
    created_at: datetime
    # Rich display data
    description: str | None = None
    origin: str | None = None
    special_move: str | None = None
    fight_style: str | None = None
    rank: int | None = None

    @computed_field
    @property
    def win_rate(self) -> float:
        if self.matches_played == 0:
            return 0.0
        return round(self.matches_won / self.matches_played, 4)

    model_config = {"from_attributes": True}


class FighterCreate(BaseModel):
    name: str
    slug: str
    character: str
    character_id: int
    llm_model: str
    image_url: str | None = None
    agent_id: UUID | None = None
    agent_architecture: str | None = None
    description: str | None = None
    origin: str | None = None
    special_move: str | None = None
    fight_style: str | None = None
    rank: int | None = None


class FighterUpdate(BaseModel):
    """Fighter update schema — all fields optional."""
    name: str | None = None
    image_url: str | None = None
    llm_model: str | None = None
    agent_id: UUID | None = None
    agent_architecture: str | None = None
    description: str | None = None
    origin: str | None = None
    special_move: str | None = None
    fight_style: str | None = None
    rank: int | None = None
