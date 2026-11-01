"""Clean up test entities provisioned for browser acceptance testing (STEP 04-B2-A)."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import delete
from api.config import get_api_settings
from api.database.connection import close_database, get_session_factory, init_database
from api.database.models import (
    CommercialAuditLogDB,
    CommercialPlanDB,
    ScheduledSubscriptionTransitionDB,
    TenantDB,
    TenantSubscriptionDB,
    UserDB,
)


async def cleanup(admin_email: str, owner_email: str, target_tnt_id: str, owner_tnt_id: str, trial_tnt_id: str = None, draft_plan_id: str = None):
    db_url = os.environ.get(
        "GID_TEST_DATABASE_URL",
        "postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown_test",
    )
    await init_database(db_url)
    factory = get_session_factory()
    tenant_ids = [t for t in [target_tnt_id, owner_tnt_id, trial_tnt_id] if t]

    async with factory() as session:
        # Delete transitions, audit logs, subs, tenants
        if tenant_ids:
            await session.execute(
                delete(ScheduledSubscriptionTransitionDB).where(
                    ScheduledSubscriptionTransitionDB.tenant_id.in_(tenant_ids)
                )
            )
            await session.execute(
                delete(CommercialAuditLogDB).where(
                    CommercialAuditLogDB.tenant_id.in_(tenant_ids)
                )
            )
            await session.execute(
                delete(TenantSubscriptionDB).where(
                    TenantSubscriptionDB.tenant_id.in_(tenant_ids)
                )
            )
            await session.execute(
                delete(TenantDB).where(
                    TenantDB.id.in_(tenant_ids)
                )
            )

        if draft_plan_id:
            await session.execute(
                delete(CommercialPlanDB).where(CommercialPlanDB.id == draft_plan_id)
            )

        emails = [e for e in [admin_email, owner_email] if e]
        if emails:
            await session.execute(
                delete(CommercialAuditLogDB).where(
                    CommercialAuditLogDB.actor_user_id.in_(
                        session.query(UserDB.id).where(UserDB.email.in_(emails))
                    )
                )
            ) if hasattr(session, "query") else None
            await session.execute(delete(UserDB).where(UserDB.email.in_(emails)))

        await session.commit()
    await close_database()


if __name__ == "__main__":
    if len(sys.argv) >= 5:
        trial_t = sys.argv[5] if len(sys.argv) > 5 else None
        draft_p = sys.argv[6] if len(sys.argv) > 6 else None
        asyncio.run(cleanup(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], trial_t, draft_p))
