from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Configure connection pooling for better performance and resource management
# pool_size: Number of connections to maintain in the pool
# max_overflow: Maximum overflow connections above pool_size
# pool_pre_ping: Test connections before using them (handles stale connections)
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=20,  # Maintain 20 connections in the pool
    max_overflow=10,  # Allow up to 10 additional connections when needed
    pool_pre_ping=True,  # Test connections before use to handle network issues
    pool_recycle=3600,  # Recycle connections after 1 hour to prevent timeout issues
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
