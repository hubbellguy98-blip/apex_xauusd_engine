"""Database connection pool and session management."""

"""
Apex Engine - Asynchronous Database Engine Core
Responsibility: Connects components to async connection pools using asyncpg drivers.
Latency Profile: Highly efficient connection pool routing, non-blocking I/O paths.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from pydantic import SecretStr

def create_apex_async_engine(database_url: SecretStr, echo_queries: bool = False) -> AsyncEngine:
    """Configures an asynchronous SQLAlchemy connection instance with optimized parameters."""
    return create_async_engine(
        database_url.get_secret_value(),
        echo=echo_queries,
        pool_size=20,
        max_overflow=10,
        pool_timeout=30.0,
        pool_recycle=1800,
        pool_pre_ping=True
    )

"""
Apex Engine - Asynchronous Session Manager Factory
Responsibility: Coordinates thread-safe transaction allocations using async scopes.
Latency Profile: Single-threaded async context safe allocation structures.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, AsyncEngine

def create_async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Returns an isolated factory instance configured for async connection pools."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False
    )