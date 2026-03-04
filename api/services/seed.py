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
