"""
Root conftest — sets up async test client with SEPARATE test database.

⚠️ CRITICAL SAFETY: Uses TEST_DATABASE_URL env var to prevent wiping production data.

Uses NullPool so each session gets its own dedicated connection,
avoiding asyncpg "another operation in progress" errors.
The app's get_db is also overridden to use the test engine.

Test data is cleaned up after each test that uses the `cleanup` fixture.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.config import settings
from app.db.models import (
    Bet,
    ChatMessage,
    Fighter,
    FighterMatchupSavestate,
    Match,
    MatchEvent,
    Stream,
    User,
)
from app.dependencies import get_current_user, get_db, require_admin
from app.main import app

# ⚠️ CRITICAL: Use a separate test database to avoid wiping production data
# If TEST_DATABASE_URL env var is not set, fall back to in-memory SQLite
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

if TEST_DATABASE_URL == settings.database_url:
    raise RuntimeError(
        "❌ CRITICAL: Test database URL matches production database URL!\n"
        f"   Production DB: {settings.database_url}\n"
        f"   Test DB:       {TEST_DATABASE_URL}\n\n"
        "Set TEST_DATABASE_URL environment variable to a separate test database:\n"
        "   export TEST_DATABASE_URL='postgresql+asyncpg://user:pass@localhost/imk_test'\n\n"
        "Or use in-memory SQLite (default): unset TEST_DATABASE_URL"
    )

# Test engine - use StaticPool for in-memory SQLite (preserves tables), NullPool for Postgres
# StaticPool keeps a single connection alive for in-memory databases
# NullPool creates fresh connections for Postgres (avoids "another operation in progress")
if "sqlite" in TEST_DATABASE_URL and ":memory:" in TEST_DATABASE_URL:
    _test_engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False}
    )
else:
    _test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

_test_session = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)

# Keep service-layer DB usage (app.db.engine.async_session) on the same test DB.
from app.db import engine as _db_engine
_db_engine.async_session = _test_session


async def _create_tables():
    """Create all tables in the test database."""
    from app.db.models import Base
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _override_get_db():
    """Override for app's get_db — uses the test engine with NullPool."""
    async with _test_session() as session:
        yield session


# ── Setup fixture to create tables ──

@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Create all database tables before running tests."""
    import asyncio
    settings.use_devnet = True
    settings.auto_queue_enabled = False
    asyncio.run(_create_tables())


# ── Test user fixtures ──

@pytest_asyncio.fixture
async def test_user() -> User:
    """Create and return a test user."""
    async with _test_session() as db:
        user = User(
            privy_user_id=f"test-privy-{uuid.uuid4().hex[:8]}",
            wallet_address=f"So1{uuid.uuid4().hex[:40]}",
            email="test@immortalkombat.com",
            display_name="Test User",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


@pytest_asyncio.fixture
async def admin_user() -> User:
    """Create and return an admin user."""
    async with _test_session() as db:
        user = User(
            privy_user_id=f"admin-privy-{uuid.uuid4().hex[:8]}",
            wallet_address=f"So1{uuid.uuid4().hex[:40]}",
            email="admin@immortalkombat.com",
            display_name="Admin",
            is_admin=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


# ── Test data fixtures ──

@pytest_asyncio.fixture
async def fighters() -> tuple[Fighter, Fighter]:
    """Create two test fighters."""
    async with _test_session() as db:
        f1 = Fighter(
            name=f"Fighter Alpha {uuid.uuid4().hex[:4]}",
            slug=f"alpha-{uuid.uuid4().hex[:6]}",
            character="MK4",
            character_id=1,
            llm_model="random",
        )
        f2 = Fighter(
            name=f"Fighter Beta {uuid.uuid4().hex[:4]}",
            slug=f"beta-{uuid.uuid4().hex[:6]}",
            character="MK4",
            character_id=2,
            llm_model="random",
        )
        db.add_all([f1, f2])
        await db.commit()
        await db.refresh(f1)
        await db.refresh(f2)
        return f1, f2


@pytest_asyncio.fixture
async def match_with_stream(fighters: tuple[Fighter, Fighter]) -> Match:
    """Create a test match in UPCOMING status with a stream."""
    f1, f2 = fighters
    async with _test_session() as db:
        match = Match(
            fighter1_id=f1.id,
            fighter2_id=f2.id,
            p1_agent="random",
            p2_agent="random",
            scheduled_at=datetime.now(timezone.utc),
            label="Test Match",
        )
        db.add(match)
        await db.flush()
        stream = Stream(match_id=match.id)
        db.add(stream)
        await db.commit()
        await db.refresh(match)
        return match


@pytest_asyncio.fixture
async def live_match(fighters: tuple[Fighter, Fighter]) -> Match:
    """Create a LIVE match."""
    f1, f2 = fighters
    async with _test_session() as db:
        match = Match(
            fighter1_id=f1.id,
            fighter2_id=f2.id,
            p1_agent="random",
            p2_agent="random",
            status="live",
            scheduled_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            label="Live Test Match",
        )
        db.add(match)
        await db.flush()
        stream = Stream(match_id=match.id, status="live")
        db.add(stream)
        await db.commit()
        await db.refresh(match)
        return match


# ── Cleanup ──

@pytest_asyncio.fixture
async def cleanup():
    """Cleanup ALL test data after the test (ordered by FK dependencies)."""
    yield
    # Clear stale match runners left by failed start attempts
    from app.services.match_runner import _active_runners
    _active_runners.clear()

    async with _test_session() as db:
        for model in (
            ChatMessage,
            MatchEvent,
            Bet,
            Stream,
            FighterMatchupSavestate,
            Match,
            Fighter,
            User,
        ):
            await db.execute(delete(model))
        await db.commit()


@pytest_asyncio.fixture(autouse=True)
async def _reset_db_between_tests():
    """Ensure each test runs with an isolated database state."""
    async with _test_session() as db:
        for model in (ChatMessage, MatchEvent, Bet, Stream, Match, Fighter, User):
            await db.execute(delete(model))
        await db.commit()
    yield
    async with _test_session() as db:
        for model in (ChatMessage, MatchEvent, Bet, Stream, Match, Fighter, User):
            await db.execute(delete(model))
        await db.commit()


# ── DB session for test assertions ──

@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Standalone DB session for assertions inside tests."""
    async with _test_session() as session:
        yield session


@pytest_asyncio.fixture
async def test_db(db: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Backward-compatible alias used by older test modules."""
    yield db


# ── HTTP client with auth override ──

@pytest_asyncio.fixture
async def client(test_user: User) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with auth mocked to return test_user."""
    async def _require_admin_override():
        raise HTTPException(403, "Admin access required")

    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[require_admin] = _require_admin_override
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client(admin_user: User) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with auth mocked to return admin_user."""
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[require_admin] = lambda: admin_user
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def unauthed_client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with no auth override (unauthenticated)."""
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
