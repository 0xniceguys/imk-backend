"""
Root conftest — sets up async test client with real Postgres DB.

Uses NullPool so each session gets its own dedicated connection,
avoiding asyncpg "another operation in progress" errors.
The app's get_db is also overridden to use the test engine.

Test data is cleaned up after each test that uses the `cleanup` fixture.
"""

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.models import Bet, ChatMessage, Fighter, Match, MatchEvent, Stream, User
from app.dependencies import get_current_user, get_db, require_admin
from app.main import app

# Test engine with NullPool — each session gets its own connection.
_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_session = async_sessionmaker(_test_engine, class_=AsyncSession, expire_on_commit=False)


async def _override_get_db():
    """Override for app's get_db — uses the test engine with NullPool."""
    async with _test_session() as session:
        yield session


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
        for model in (ChatMessage, MatchEvent, Bet, Stream, Match, Fighter, User):
            await db.execute(delete(model))
        await db.commit()


# ── DB session for test assertions ──

@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Standalone DB session for assertions inside tests."""
    async with _test_session() as session:
        yield session


# ── HTTP client with auth override ──

@pytest_asyncio.fixture
async def client(test_user: User) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with auth mocked to return test_user."""
    app.dependency_overrides[get_current_user] = lambda: test_user
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
