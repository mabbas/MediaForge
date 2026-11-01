"""Provision test entities in PostgreSQL for browser acceptance testing (STEP 04-B2-A)."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sys
import uuid

# Add workspace root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import select
from api.config import get_api_settings
from api.database.connection import close_database, get_session_factory, init_database
from api.database.models import (
    CommercialPlanDB,
    CommercialPlanEntitlementDB,
    ScheduledSubscriptionTransitionDB,
    TenantDB,
    TenantSubscriptionDB,
    UserDB,
)
from api.services.subscription_lifecycle_service import update_scheduler_config
from api.security.passwords import hash_password


async def setup():
    db_url = os.environ.get(
        "GID_TEST_DATABASE_URL",
        "postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown_test",
    )
    await init_database(db_url)
    factory = get_session_factory()
    uid = uuid.uuid4().hex[:8]
    pwd_hash = hash_password("BrowserAdmin123!")

    async with factory() as session:
        # 0. Ensure default root tenant exists
        root_tenant = (
            await session.execute(
                select(TenantDB).where(TenantDB.id == "00000000-0000-0000-0000-000000000001")
            )
        ).scalars().first()
        if not root_tenant:
            root_tenant = TenantDB(
                id="00000000-0000-0000-0000-000000000001",
                name="Default Tenant",
                slug="default",
                tier="free",
                is_active=True,
            )
            session.add(root_tenant)
            await session.flush()

        # 1. Fetch free plan and trial plan
        free_plan = (
            await session.execute(
                select(CommercialPlanDB).where(
                    CommercialPlanDB.plan_code == "plan-free-v1",
                    CommercialPlanDB.status == "active",
                )
            )
        ).scalars().first()
        if not free_plan:
            free_plan = (
                await session.execute(
                    select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
                )
            ).scalars().first()

        trial_plan = (
            await session.execute(
                select(CommercialPlanDB).where(
                    CommercialPlanDB.plan_code == "plan-trial-v1",
                    CommercialPlanDB.status == "active",
                )
            )
        ).scalars().first()
        if not trial_plan:
            trial_plan = free_plan

        # 2. Admin User
        admin_email = f"admin_{uid}@grabitdown.local"
        admin_user = UserDB(
            id=f"usr_adm_{uid}",
            tenant_id="00000000-0000-0000-0000-000000000001",
            email=admin_email,
            password_hash=pwd_hash,
            role="system_admin",
            is_active=True,
        )

        # 3. Owner Tenant & User (Non-admin)
        t_owner_id = f"tnt_own_{uid}"
        t_owner = TenantDB(
            id=t_owner_id,
            name=f"Owner Tenant {uid}",
            slug=f"own-{uid}",
            tier="basic",
            is_active=True,
        )
        owner_email = f"owner_{uid}@example.com"
        owner_user = UserDB(
            id=f"usr_own_{uid}",
            tenant_id="00000000-0000-0000-0000-000000000001",
            email=owner_email,
            password_hash=pwd_hash,
            role="member",
            is_active=True,
        )

        # 4. Target Tenant (starts active on free_plan)
        target_tnt_id = f"tnt_target_{uid}"
        target_tnt = TenantDB(
            id=target_tnt_id,
            name=f"Browser Target Tenant {uid}",
            slug=f"tgt-{uid}",
            tier="basic",
            is_active=True,
        )
        target_sub = TenantSubscriptionDB(
            id=f"sub_tgt_{uid}",
            tenant_id=target_tnt_id,
            plan_id=free_plan.id,
            status="active",
        )

        # 5. Trial Tenant (status trial, trial_days_remaining 12)
        trial_tnt_id = f"tnt_trial_{uid}"
        trial_tnt = TenantDB(
            id=trial_tnt_id,
            name=f"Browser Trial Tenant {uid}",
            slug=f"trl-{uid}",
            tier="trial",
            is_active=True,
        )
        now_dt = datetime.utcnow()
        trial_end = now_dt + timedelta(days=12)
        trial_sub = TenantSubscriptionDB(
            id=f"sub_trl_{uid}",
            tenant_id=trial_tnt_id,
            plan_id=trial_plan.id,
            status="trial",
            trial_started_at=now_dt,
            trial_expires_at=trial_end,
            current_period_end=trial_end,
        )

        # 6. Draft Plan with unapproved pricing
        draft_plan_id = f"plan_draft_{uid}"
        draft_plan_code = f"plan-draft-{uid}"
        draft_plan_name = f"Draft Plan {uid}"
        draft_plan = CommercialPlanDB(
            id=draft_plan_id,
            plan_code=draft_plan_code,
            name=draft_plan_name,
            version=1,
            tier_type="paid",
            status="draft",
            price_cents=None,
            billing_interval="monthly",
            metadata_json={"pricing_status": "unapproved_pending_commercial_review"},
        )
        draft_entitlement = CommercialPlanEntitlementDB(
            id=f"ent_{uid}",
            plan_id=draft_plan_id,
            entitlement_key="max_concurrency",
            value_type="concurrency",
            value_int=2,
            is_unlimited=False,
        )

        # 7. Cancellable Scheduled Transition on trial_tnt (effective 30 days from now)
        cancel_trans_id = f"trans_cnl_{uid}"
        future_effective = now_dt + timedelta(days=30)
        cancellable_transition = ScheduledSubscriptionTransitionDB(
            id=cancel_trans_id,
            tenant_id=trial_tnt_id,
            target_plan_id=free_plan.id,
            transition_type="downgrade",
            status="pending",
            scheduled_at=future_effective,
            reason="Scheduled for future cancellation test",
        )

        session.add_all([
            admin_user,
            t_owner,
            owner_user,
            target_tnt,
            target_sub,
            trial_tnt,
            trial_sub,
            draft_plan,
            draft_entitlement,
            cancellable_transition,
        ])
        await session.commit()

        # 8. Configure scheduler on grabitdown_test with allowlist for target_tnt_id and fast poll
        await update_scheduler_config(
            session,
            paused=False,
            rollout_mode="allowlist_only",
            tenant_allowlist=[target_tnt_id],
            poll_interval_seconds=2,
            reason="Setup test allowlist and 2s poll for browser acceptance",
            actor_user_id=admin_user.id,
            actor_role="system_admin",
        )
        await session.commit()

        print(
            json.dumps({
                "admin_email": admin_email,
                "owner_email": owner_email,
                "password": "BrowserAdmin123!",
                "target_tnt_id": target_tnt_id,
                "target_name": f"Browser Target Tenant {uid}",
                "owner_tenant_id": t_owner_id,
                "trial_tnt_id": trial_tnt_id,
                "draft_plan_id": draft_plan_id,
                "draft_plan_name": draft_plan_name,
                "draft_plan_code": draft_plan_code,
                "cancellable_transition_id": cancel_trans_id,
            })
        )

    await close_database()


if __name__ == "__main__":
    asyncio.run(setup())
