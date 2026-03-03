import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


# ── Enums ──


class MatchStatus(str, enum.Enum):
    UPCOMING = "upcoming"
    LIVE = "live"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BetStatus(str, enum.Enum):
    ACTIVE = "active"
    WON = "won"
    LOST = "lost"
    CANCELLED = "cancelled"


class StreamStatus(str, enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    LIVE = "live"
    STOPPED = "stopped"
    ERROR = "error"


# ── Models ──


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    privy_user_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    wallet_address: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    bets: Mapped[list["Bet"]] = relationship(back_populates="user")


class Agent(Base):
    """Uploaded neural network agents (ONNX checkpoints)."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    architecture: Mapped[str] = mapped_column(String(50), nullable=False)  # lstm, transformer, etc.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_path: Mapped[str] = mapped_column(String(500), nullable=False)  # path to .onnx file
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    uploader: Mapped["User | None"] = relationship(foreign_keys=[uploaded_by])


class Fighter(Base):
    __tablename__ = "fighters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    character: Mapped[str] = mapped_column(String(50), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Agent can be either:
    # 1. Built-in agent (agent_architecture = "random", "cpu", etc.)
    # 2. Custom uploaded agent (agent_id = UUID of Agent record)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    agent_architecture: Mapped[str | None] = mapped_column(String(50), nullable=True)  # fallback for built-ins

    # Deprecated: keeping for backward compatibility, will be removed in migration
    agent_checkpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)

    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    matches_won: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    agent: Mapped["Agent | None"] = relationship(foreign_keys=[agent_id])

    @property
    def win_rate(self) -> float:
        if self.matches_played == 0:
            return 0.0
        return self.matches_won / self.matches_played


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fighter1_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fighters.id"), nullable=True
    )
    fighter2_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fighters.id"), nullable=True
    )
    p1_agent: Mapped[str] = mapped_column(String(50), default="random", nullable=False)
    p2_agent: Mapped[str] = mapped_column(String(50), default="random", nullable=False)
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus), default=MatchStatus.UPCOMING, nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(100), default="MK4-Classic")
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fighters.id"), nullable=True
    )
    savestate_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    emulator_instance_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    socket_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    best_of: Mapped[int] = mapped_column(Integer, default=3, nullable=False, server_default="3")
    current_round: Mapped[int] = mapped_column(Integer, default=1, nullable=False, server_default="1")
    rounds_won_p1: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    rounds_won_p2: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    fighter1: Mapped["Fighter"] = relationship(foreign_keys=[fighter1_id])
    fighter2: Mapped["Fighter"] = relationship(foreign_keys=[fighter2_id])
    winner: Mapped["Fighter | None"] = relationship(foreign_keys=[winner_id])
    bets: Mapped[list["Bet"]] = relationship(back_populates="match")
    events: Mapped[list["MatchEvent"]] = relationship(back_populates="match")
    stream: Mapped["Stream | None"] = relationship(back_populates="match", uselist=False)


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False, index=True
    )
    fighter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fighters.id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="SOL")
    odds_at_placement: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    status: Mapped[BetStatus] = mapped_column(
        Enum(BetStatus), default=BetStatus.ACTIVE, nullable=False
    )
    payout: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    tx_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="bets")
    match: Mapped["Match"] = relationship(back_populates="bets")
    fighter: Mapped["Fighter"] = relationship()


class MatchEvent(Base):
    __tablename__ = "match_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    frame_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    match: Mapped["Match"] = relationship(back_populates="events")


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), unique=True, nullable=False
    )
    status: Mapped[StreamStatus] = mapped_column(
        Enum(StreamStatus), default=StreamStatus.IDLE
    )
    hls_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    vod_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ffmpeg_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    viewer_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    match: Mapped["Match"] = relationship(back_populates="stream")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()
    match: Mapped["Match"] = relationship()
