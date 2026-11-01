"""Check database state in grabitdown_test for browser acceptance suite."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import select
from api.database.connection import close_database, get_session_factory, init_database
from api.database.models import (
    CommercialPlanDB,
    ScheduledSubscriptionTransitionDB,
    TenantSubscriptionDB,
)


async def check(cmd: str, arg: str):
    db_url = os.environ.get(
        "GID_TEST_DATABASE_URL",
        "postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown_test",
    )
    await init_database(db_url)
    factory = get_session_factory()

    async with factory() as session:
        if cmd == "get_transition_status":
            tenant_id = arg
            trans = (
                await session.execute(
                    select(ScheduledSubscriptionTransitionDB)
                    .where(ScheduledSubscriptionTransitionDB.tenant_id == tenant_id)
                    .order_by(ScheduledSubscriptionTransitionDB.created_at.desc())
                )
            ).scalars().first()
            if trans:
                print(json.dumps({"status": trans.status, "id": trans.id, "target_plan_id": trans.target_plan_id}))
            else:
                print(json.dumps({"status": None}))

        elif cmd == "make_transition_due":
            tenant_id = arg
            trans = (
                await session.execute(
                    select(ScheduledSubscriptionTransitionDB)
                    .where(ScheduledSubscriptionTransitionDB.tenant_id == tenant_id, ScheduledSubscriptionTransitionDB.status == "pending")
                    .order_by(ScheduledSubscriptionTransitionDB.created_at.desc())
                )
            ).scalars().first()
            if trans:
                from datetime import datetime, timedelta
                trans.scheduled_at = datetime.utcnow() - timedelta(seconds=5)
                await session.commit()
                print(json.dumps({"updated": True, "id": trans.id}))
            else:
                print(json.dumps({"updated": False}))

        elif cmd == "get_subscription":
            tenant_id = arg
            sub = (
                await session.execute(
                    select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
                )
            ).scalars().first()
            if sub:
                plan = (
                    await session.execute(
                        select(CommercialPlanDB).where(CommercialPlanDB.id == sub.plan_id)
                    )
                ).scalars().first()
                print(json.dumps({
                    "plan_id": sub.plan_id,
                    "plan_code": plan.plan_code if plan else None,
                    "plan_name": plan.name if plan else None,
                    "status": sub.status,
                }))
            else:
                print(json.dumps({"subscription": None}))

    await close_database()


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        asyncio.run(check(sys.argv[1], sys.argv[2]))
