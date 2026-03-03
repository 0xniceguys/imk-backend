"""Pydantic schemas for Agent resources."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AgentOut(BaseModel):
    """Agent output schema."""

    id: UUID
    name: str
    slug: str
    architecture: str
    description: str | None = None
    file_size_bytes: int
    uploaded_by: UUID | None = None
    is_public: bool = True
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class AgentCreate(BaseModel):
    """Agent creation schema (for metadata)."""

    name: str = Field(..., min_length=3, max_length=100)
    slug: str = Field(..., min_length=3, max_length=50, pattern=r"^[a-z0-9_-]+$")
    architecture: str = Field(..., pattern=r"^(lstm|transformer|disc_rssm|obj_belief)$")
    description: str | None = Field(None, max_length=1000)
    is_public: bool = True


class AgentUpdate(BaseModel):
    """Agent update schema."""

    name: str | None = Field(None, min_length=3, max_length=100)
    description: str | None = Field(None, max_length=1000)
    is_public: bool | None = None
