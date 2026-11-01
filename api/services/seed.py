"""Database seeding — creates default tenant and system user on first startup."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.models import TenantDB, UserDB

logger = logging.getLogger(__name__)


async def seed_defaults(session: AsyncSession) -> None:
    """Create default tenant and system user if they don't exist."""

    # Check if default tenant exists
    result = await session.execute(select(TenantDB).where(TenantDB.slug == "default"))
    tenant = result.scalars().one_or_none()

    if not tenant:
        tenant = TenantDB(
            id="00000000-0000-0000-0000-000000000001",
            name="Default",
            slug="default",
            tier="platinum",
            settings={"mode": "personal", "created_by": "system"},
            is_active=True,
        )
        session.add(tenant)
        logger.info("Created default tenant")

    # Check if system user exists
    result = await session.execute(
        select(UserDB).where(UserDB.id == "00000000-0000-0000-0000-000000000002")
    )
    user = result.scalars().one_or_none()

    if not user:
        user = UserDB(
            id="00000000-0000-0000-0000-000000000002",
            tenant_id=tenant.id,
            email="system@grabitdown.local",
            display_name="System User",
            auth_provider="local",
            role="owner",
            tier_override="platinum",
            is_active=True,
        )
        session.add(user)
        logger.info("Created system user")

    await session.commit()
    logger.info("Database seeding complete")


async def run_seed(database_url: str | None = None) -> None:
    """Explicitly seed default tenant and user.

    Never invoked automatically on application startup.
    Idempotent: safe to run multiple times.
    """
    import api.database.connection as db_conn
    from api.config import get_api_settings

    settings = get_api_settings()
    url = database_url or settings.database_url

    # Initialize connection without creating tables or migrating
    await db_conn.init_database(database_url=url, validate_revision=False)
    if db_conn.async_session_factory is None:
        raise RuntimeError("Failed to initialize database session factory for seeding.")

    async with db_conn.async_session_factory() as session:
        await seed_defaults(session)

    await db_conn.close_database()
    logger.info("Explicit seeding execution completed successfully.")


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_seed())
