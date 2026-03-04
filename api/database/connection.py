"""GrabItDown database connection management.

Supports PostgreSQL (production) and SQLite (testing).
Uses connection pooling for PostgreSQL.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData

logger = logging.getLogger(__name__)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """SQLAlchemy declarative base with naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


engine: AsyncEngine | None = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_database(
    database_url: str,
    schema: str = "gid",
    pool_size: int = 10,
    max_overflow: int = 20,
    echo: bool = False,
) -> None:
    """Initialize database engine and create tables.

    Supports both PostgreSQL and SQLite URLs.
    PostgreSQL uses connection pooling.
    SQLite uses NullPool (for testing).
    """
    global engine, async_session_factory

    is_sqlite = "sqlite" in database_url

    engine_kwargs: dict = {
        "echo": echo,
    }

    if is_sqlite:
        from sqlalchemy.pool import NullPool

        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs.update(
            {
                "pool_size": pool_size,
                "max_overflow": max_overflow,
                "pool_pre_ping": True,
                "pool_recycle": 3600,
            }
        )

    engine = create_async_engine(database_url, **engine_kwargs)

    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Import models so they are registered on Base.metadata before create_all
    from api.database import models  # noqa: F401

    # Create all tables in the default schema for both PostgreSQL and SQLite.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    db_type = "SQLite" if is_sqlite else "PostgreSQL"
    display_url = (
        database_url.split("@")[-1] if "@" in database_url else database_url
    )
    logger.info(f"Database initialized ({db_type}): {display_url}")


async def close_database() -> None:
    """Close database connections and pool."""
    global engine
    if engine:
        await engine.dispose()
        engine = None
        logger.info("Database connections closed")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: get async database session."""
    if async_session_factory is None:
        raise RuntimeError(
            "Database not initialized. Call init_database() first."
        )

    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
