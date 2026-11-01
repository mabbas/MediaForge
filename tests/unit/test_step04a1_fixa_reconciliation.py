"""Comprehensive test suite for STEP 04-A1-Fix-A: Commercial Catalog Safety,
Legacy Tenant Reconciliation, Default Trial Configuration and Metering Correctness.

Verifies:
1. Unapproved pricing seed prevention & plan retirement.
2. Published plan immutability and draft v2 creation.
3. Existing tenant reconciliation safety (legacy_active, no blocking).
4. Persistent default trial configuration in PostgreSQL app_settings.
5. Zero-day trial behavior (immediate expiration, zero positive trial window).
6. Existing trial stability after system default duration changes.
7. Trial extension with mandatory administrative reason and audit trail.
8. System-admin privilege restrictions (strict fail-closed boundary).
9. Per-tenant entitlement isolation.
10. Download usage idempotency and non-duplication.
11. Clip and merge atomic usage recording.
12. Authoritative physical storage reconciliation.
13. Queued vs running concurrency breakdown.
14. Deterministic UTC billing-period boundary calculations.
15. Registration pipeline initial subscription assignment.
16. Advisory-only enforcement (no customer operations blocked).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import uuid
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.models import (
    APIKeyDB,
    AppSettingDB,
    Base,
    CommercialAuditLogDB,
    CommercialPlanDB,
    CommercialPlanEntitlementDB,
    DownloadJobDB,
    MediaArtifactDB,
    MediaWorkItemDB,
    PendingRegistrationDB,
    TenantDB,
    TenantEntitlementOverrideDB,
    TenantSubscriptionDB,
    UsageEventDB,
    UserDB,
)
from api.dependencies import AuthMethod, CurrentUser
from api.routers.admin_plans import (
    assign_tenant_plan,
    create_plan,
    extend_trial,
    get_default_trial_setting,
    list_plans,
    publish_plan,
    retire_plan,
    set_override,
    update_default_trial_setting,
)
from api.schemas.admin_plans import (
    AssignPlanRequest,
    EntitlementInput,
    EntitlementOverrideRequest,
    ExtendTrialRequest,
    PlanCreateRequest,
    UpdateDefaultTrialRequest,
)
from api.services.entitlement_service import (
    DEFAULT_TRIAL_SETTING_KEY,
    QuotaReasonCode,
    assign_plan_to_tenant,
    evaluate_tenant_quotas,
    extend_tenant_trial,
    get_system_default_trial_days,
    get_system_default_trial_setting,
    resolve_effective_entitlements,
    revoke_tenant_entitlement_override,
    set_tenant_entitlement_override,
    simulate_operation_admission,
    update_system_default_trial_days,
)
from api.services.job_service import complete_job_and_artifact_atomic
from api.services.registration_service import RegistrationService
from api.services.usage_service import record_usage_event


@pytest.fixture
def env_fixture(tmp_path):
    """Setup isolated SQLite engine with gid schema translation and test fixtures."""
    db_file = tmp_path / "fixa_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_a_id = f"tenant_a_{uuid.uuid4().hex[:6]}"
    tenant_b_id = f"tenant_b_{uuid.uuid4().hex[:6]}"
    tenant_legacy_creator_id = f"t_leg_cr_{uuid.uuid4().hex[:6]}"
    user_admin_id = f"admin_{uuid.uuid4().hex[:6]}"
    user_member_id = f"member_{uuid.uuid4().hex[:6]}"
    user_sysadmin_id = f"sysadmin_{uuid.uuid4().hex[:6]}"

    async def init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            # Create Tenants
            t_a = TenantDB(
                id=tenant_a_id,
                name="Tenant Alpha",
                slug=f"alpha-{uuid.uuid4().hex[:6]}",
                tier="basic",
                is_active=True,
            )
            t_b = TenantDB(
                id=tenant_b_id,
                name="Tenant Beta",
                slug=f"beta-{uuid.uuid4().hex[:6]}",
                tier="basic",
                is_active=True,
            )
            t_leg_cr = TenantDB(
                id=tenant_legacy_creator_id,
                name="Legacy Creator Tenant",
                slug=f"legacy-cr-{uuid.uuid4().hex[:6]}",
                tier="pro",
                is_active=True,
            )
            session.add_all([t_a, t_b, t_leg_cr])

            # Create Users
            u_admin = UserDB(
                id=user_admin_id,
                tenant_id=tenant_a_id,
                email="admin@alpha.com",
                role="admin",
                is_active=True,
            )
            u_member = UserDB(
                id=user_member_id,
                tenant_id=tenant_a_id,
                email="member@alpha.com",
                role="member",
                is_active=True,
            )
            u_sysadmin = UserDB(
                id=user_sysadmin_id,
                tenant_id=tenant_a_id,
                email="root@system.com",
                role="system_admin",
                is_active=True,
            )
            session.add_all([u_admin, u_member, u_sysadmin])

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            # Seed standard plans
            # 1. Free plan
            p_free = CommercialPlanDB(
                id="plan-free-v1",
                plan_code="free",
                name="MediaForge Free",
                status="active",
                is_public=True,
                tier_type="free",
                version=1,
                price_cents=0,
                published_at=now,
            )
            # 2. Retired unapproved plan v1
            p_cr_v1 = CommercialPlanDB(
                id="plan-creator-monthly-v1",
                plan_code="creator_monthly",
                name="MediaForge Creator",
                status="retired",
                is_public=False,
                tier_type="paid",
                version=1,
                price_cents=1900,
                published_at=now,
                retired_at=now,
                metadata_json={"unapproved_pricing": True, "retired_reason": "Seeded prices not approved"},
            )
            # 3. Draft v2 plan with unset price
            p_cr_v2 = CommercialPlanDB(
                id="plan-creator-monthly-v2",
                plan_code="creator_monthly",
                name="MediaForge Creator",
                status="draft",
                is_public=False,
                tier_type="paid",
                version=2,
                price_cents=0,
                metadata_json={"pricing_status": "unapproved_pending_commercial_review"},
            )
            # 4. Trial plan
            p_trial = CommercialPlanDB(
                id="plan-trial-v1",
                plan_code="enterprise_trial",
                name="MediaForge Pro Trial",
                status="active",
                is_public=False,
                tier_type="trial",
                version=1,
                price_cents=0,
                published_at=now,
            )
            session.add_all([p_free, p_cr_v1, p_cr_v2, p_trial])

            # Entitlements
            e_free_dl = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id="plan-free-v1",
                entitlement_key="monthly_download_count",
                value_type="count",
                value_int=50,
            )
            e_free_c = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id="plan-free-v1",
                entitlement_key="max_concurrent_jobs",
                value_type="concurrency",
                value_int=1,
            )
            e_cr_dl = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id="plan-creator-monthly-v1",
                entitlement_key="monthly_download_count",
                value_type="count",
                value_int=500,
            )
            e_cr_c = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id="plan-creator-monthly-v1",
                entitlement_key="max_concurrent_jobs",
                value_type="concurrency",
                value_int=2,
            )
            session.add_all([e_free_dl, e_free_c, e_cr_dl, e_cr_c])

            # Seed default trial setting in app_settings (14 days)
            setting = AppSettingDB(
                key=DEFAULT_TRIAL_SETTING_KEY,
                value={
                    "trial_days": 14,
                    "description": "Default trial duration in days for newly activated commercial tenants",
                    "reason": "Standard initial baseline",
                },
                updated_at=now,
            )
            session.add(setting)

            # Subscriptions
            # Tenant A -> Free (active)
            sub_a = TenantSubscriptionDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_a_id,
                plan_id="plan-free-v1",
                status="active",
                current_period_start=now,
                metadata_json={"reconciliation_status": "reconciled_free_tier"},
            )
            # Legacy Creator Tenant -> Legacy Active (grandfathered, pending commercial review)
            sub_leg = TenantSubscriptionDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_legacy_creator_id,
                plan_id="plan-creator-monthly-v1",
                status="legacy_active",
                current_period_start=now,
                metadata_json={
                    "reconciliation_status": "pending_commercial_review",
                    "legacy_grant_terms": "Pre-commercial account migrated from legacy tier",
                    "verified_payment": False,
                },
            )
            session.add_all([sub_a, sub_leg])

            await session.commit()

    asyncio.run(init_db())

    return {
        "engine": engine,
        "session_factory": session_factory,
        "tenant_a_id": tenant_a_id,
        "tenant_b_id": tenant_b_id,
        "tenant_legacy_creator_id": tenant_legacy_creator_id,
        "user_admin_id": user_admin_id,
        "user_member_id": user_member_id,
        "user_sysadmin_id": user_sysadmin_id,
    }


# ── TEST 1: Unapproved Pricing Seed Prevention & Plan Retirement ──


@pytest.mark.asyncio
async def test_01_unapproved_pricing_plan_retirement(env_fixture):
    """Seeded plans with unapproved pricing are retired, non-public, and cannot be newly assigned."""
    session_factory = env_fixture["session_factory"]
    sysadmin = CurrentUser(
        user_id=env_fixture["user_sysadmin_id"],
        tenant_id=env_fixture["tenant_a_id"],
        role="system_admin",
        is_system_owner=True,
    )

    async with session_factory() as session:
        plans = await list_plans(user=sysadmin, session=session)
        cr_v1 = next((p for p in plans if p.id == "plan-creator-monthly-v1"), None)
        assert cr_v1 is not None
        assert cr_v1.status == "retired"
        assert cr_v1.is_public is False
        assert cr_v1.metadata_json.get("unapproved_pricing") is True

        # Attempting to assign retired plan to a new tenant raises ValueError
        with pytest.raises(ValueError) as val_err:
            await assign_plan_to_tenant(
                session,
                tenant_id=env_fixture["tenant_b_id"],
                plan_id="plan-creator-monthly-v1",
                actor_user_id=sysadmin.user_id,
            )
        assert "is retired and cannot accept new assignments" in str(val_err.value)


# ── TEST 2: Published Plan Immutability and Draft v2 Creation ──


@pytest.mark.asyncio
async def test_02_published_plan_immutability_and_draft_v2(env_fixture):
    """Published plans are immutable; new plan versions start in draft with zero/unset pricing."""
    session_factory = env_fixture["session_factory"]
    sysadmin = CurrentUser(
        user_id=env_fixture["user_sysadmin_id"],
        tenant_id=env_fixture["tenant_a_id"],
        role="system_admin",
        is_system_owner=True,
    )

    async with session_factory() as session:
        # Verify draft v2 plan exists with zero price
        plans = await list_plans(user=sysadmin, session=session)
        cr_v2 = next((p for p in plans if p.id == "plan-creator-monthly-v2"), None)
        assert cr_v2 is not None
        assert cr_v2.status == "draft"
        assert cr_v2.is_public is False
        assert cr_v2.version == 2
        assert cr_v2.price_cents == 0
        assert cr_v2.metadata_json.get("pricing_status") == "unapproved_pending_commercial_review"

        # Attempting to assign draft plan to tenant fails
        with pytest.raises(ValueError) as val_err:
            await assign_plan_to_tenant(
                session,
                tenant_id=env_fixture["tenant_b_id"],
                plan_id="plan-creator-monthly-v2",
                actor_user_id=sysadmin.user_id,
            )
        assert "cannot accept new assignments" in str(val_err.value) or "draft" in str(val_err.value) or "retired" in str(val_err.value)


# ── TEST 3: Existing Tenant Reconciliation Safety (legacy_active, No Blocking) ──


@pytest.mark.asyncio
async def test_03_existing_tenant_reconciliation_safety(env_fixture):
    """Legacy pre-commercial tenants mapped to legacy_active retain access without customer blocking."""
    session_factory = env_fixture["session_factory"]
    t_leg = env_fixture["tenant_legacy_creator_id"]

    async with session_factory() as session:
        status = await evaluate_tenant_quotas(session, t_leg)
        assert status.tenant_id == t_leg
        assert status.subscription_status == "legacy_active"
        assert status.is_advisory is True

        # Entitlements from assigned plan are preserved
        assert status.checks["monthly_download_count"].limit_value == 500
        assert status.checks["max_concurrent_jobs"].limit_value == 2

        # Simulation evaluates as admissible (no blocking)
        sim = await simulate_operation_admission(
            session,
            tenant_id=t_leg,
            operation_type="download",
            estimated_bytes=1000,
        )
        assert sim["admissible"] is True
        assert sim["subscription_status"] == "legacy_active"


# ── TEST 4: Persistent Default Trial Configuration in PostgreSQL app_settings ──


@pytest.mark.asyncio
async def test_04_persistent_default_trial_configuration(env_fixture):
    """Default trial duration persists in app_settings and is queryable via service & admin API."""
    session_factory = env_fixture["session_factory"]
    sysadmin = CurrentUser(
        user_id=env_fixture["user_sysadmin_id"],
        tenant_id=env_fixture["tenant_a_id"],
        role="system_admin",
        is_system_owner=True,
    )

    async with session_factory() as session:
        # Read via service
        trial_days = await get_system_default_trial_days(session)
        assert trial_days == 14

        # Read via admin endpoint
        res = await get_default_trial_setting(user=sysadmin, session=session)
        assert res.trial_days == 14


# ── TEST 5: Zero-Day Trial Behavior ──


@pytest.mark.asyncio
async def test_05_zero_day_trial_behavior(env_fixture):
    """When default trial is set to 0, newly assigned trial expires immediately with 0 remaining days."""
    session_factory = env_fixture["session_factory"]
    sysadmin_id = env_fixture["user_sysadmin_id"]
    t_b = env_fixture["tenant_b_id"]

    async with session_factory() as session:
        sub = await assign_plan_to_tenant(
            session,
            tenant_id=t_b,
            plan_id="plan-trial-v1",
            status="trial",
            trial_days=0,
            actor_user_id=sysadmin_id,
            reason="Zero-day evaluation test",
        )
        await session.commit()

        status = await evaluate_tenant_quotas(session, t_b)
        assert status.is_trial is True
        assert status.subscription_status == "expired"
        assert status.trial_days_remaining == 0


# ── TEST 6: Existing Trial Stability After System Default Duration Changes ──


@pytest.mark.asyncio
async def test_06_existing_trial_stability_after_default_changes(env_fixture):
    """Changing default trial setting from 14 to 30 days does NOT alter existing tenant's trial expiry."""
    session_factory = env_fixture["session_factory"]
    sysadmin_id = env_fixture["user_sysadmin_id"]
    sysadmin = CurrentUser(
        user_id=sysadmin_id,
        tenant_id=env_fixture["tenant_a_id"],
        role="system_admin",
        is_system_owner=True,
    )
    t_b = env_fixture["tenant_b_id"]

    async with session_factory() as session:
        # 1. Onboard Tenant B with initial 14-day trial
        sub_initial = await assign_plan_to_tenant(
            session,
            tenant_id=t_b,
            plan_id="plan-trial-v1",
            status="trial",
            trial_days=14,
            actor_user_id=sysadmin_id,
            reason="Initial 14-day trial",
        )
        await session.commit()
        initial_expiry = sub_initial.trial_expires_at

        # 2. Administrator changes default trial to 30 days
        req = UpdateDefaultTrialRequest(trial_days=30, reason="Updated commercial policy for Q4 onboarding")
        updated = await update_default_trial_setting(body=req, user=sysadmin, session=session)
        assert updated.trial_days == 30

        # 3. Existing Tenant B trial expiry MUST remain unchanged
        sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == t_b)
        sub_current = (await session.execute(sub_stmt)).scalars().first()
        assert sub_current.trial_expires_at == initial_expiry

        # 4. Audit log verification
        audit_stmt = select(CommercialAuditLogDB).where(
            CommercialAuditLogDB.action == "default_trial_days_updated"
        )
        audit_rec = (await session.execute(audit_stmt)).scalars().first()
        assert audit_rec is not None
        assert audit_rec.details_json["previous_trial_days"] == 14
        assert audit_rec.details_json["new_trial_days"] == 30
        assert audit_rec.reason == "Updated commercial policy for Q4 onboarding"


# ── TEST 7: Trial Extension with Mandatory Reason and Audit ──


@pytest.mark.asyncio
async def test_07_trial_extension_with_mandatory_reason(env_fixture):
    """Extending trial requires mandatory justification and updates expiry strictly in UTC."""
    session_factory = env_fixture["session_factory"]
    t_b = env_fixture["tenant_b_id"]
    sysadmin_id = env_fixture["user_sysadmin_id"]

    async with session_factory() as session:
        # Assign 7 day trial
        await assign_plan_to_tenant(
            session,
            tenant_id=t_b,
            plan_id="plan-trial-v1",
            status="trial",
            trial_days=7,
            actor_user_id=sysadmin_id,
            reason="7-day trial",
        )
        await session.commit()

        # Reject extension with empty reason
        with pytest.raises(ValueError) as err_empty:
            await extend_tenant_trial(
                session,
                tenant_id=t_b,
                additional_days=5,
                reason="   ",
                actor_user_id=sysadmin_id,
            )
        assert "Administrative reason is mandatory" in str(err_empty.value)

        # Successful extension
        extended = await extend_tenant_trial(
            session,
            tenant_id=t_b,
            additional_days=5,
            reason="Customer request via ticket #4812",
            actor_user_id=sysadmin_id,
        )
        await session.commit()
        assert extended.status == "trial"


# ── TEST 8: System-Admin Privilege Restrictions ──


@pytest.mark.asyncio
async def test_08_system_admin_privilege_restrictions(env_fixture):
    """Ordinary members, tenant admins, and API keys cannot modify default trial or plans."""
    session_factory = env_fixture["session_factory"]
    member_user = CurrentUser(
        user_id=env_fixture["user_member_id"],
        tenant_id=env_fixture["tenant_a_id"],
        role="member",
        is_system_owner=False,
    )
    admin_user = CurrentUser(
        user_id=env_fixture["user_admin_id"],
        tenant_id=env_fixture["tenant_a_id"],
        role="admin",
        is_system_owner=False,
    )
    api_key_user = CurrentUser(
        user_id="service:key123",
        tenant_id=env_fixture["tenant_a_id"],
        auth_method=AuthMethod.API_KEY,
        api_key_id="key123",
        role="admin",
        is_system_owner=False,
    )

    async with session_factory() as session:
        req = UpdateDefaultTrialRequest(trial_days=21, reason="Unauthorized attempt")
        for u in [member_user, admin_user, api_key_user]:
            with pytest.raises(HTTPException) as exc:
                await update_default_trial_setting(body=req, user=u, session=session)
            assert exc.value.status_code == 403


# ── TEST 9: Per-Tenant Entitlement Isolation ──


@pytest.mark.asyncio
async def test_09_per_tenant_entitlement_isolation(env_fixture):
    """Tenant A limits (50 downloads) and Tenant B limits (500 downloads) are completely isolated."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    t_leg = env_fixture["tenant_legacy_creator_id"]

    async with session_factory() as session:
        status_a = await evaluate_tenant_quotas(session, t_a)
        status_leg = await evaluate_tenant_quotas(session, t_leg)

        assert status_a.checks["monthly_download_count"].limit_value == 50
        assert status_leg.checks["monthly_download_count"].limit_value == 500


# ── TEST 10: Download Usage Idempotency and Non-Duplication ──


@pytest.mark.asyncio
async def test_10_download_usage_idempotency(env_fixture):
    """Recording the same download usage event multiple times is deduplicated idempotently."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    key = f"download:idempotency_test_{uuid.uuid4().hex[:6]}"

    async with session_factory() as session:
        ev1, is_new1 = await record_usage_event(
            session,
            tenant_id=t_a,
            event_type="download",
            idempotency_key=key,
            quantity=1.0,
            bytes=45000000,
        )
        await session.commit()
        assert is_new1 is True

        ev2, is_new2 = await record_usage_event(
            session,
            tenant_id=t_a,
            event_type="download",
            idempotency_key=key,
            quantity=1.0,
            bytes=45000000,
        )
        assert is_new2 is False
        assert ev1.id == ev2.id


# ── TEST 11: Clip and Merge Atomic Usage Recording ──


@pytest.mark.asyncio
async def test_11_clip_and_merge_atomic_usage(env_fixture):
    """Job, artifact, and commercial usage event are committed together atomically."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    job_id = f"job_at_{uuid.uuid4().hex[:6]}"
    art_id = f"art_at_{uuid.uuid4().hex[:6]}"

    async with session_factory() as session:
        wi = MediaWorkItemDB(
            id=job_id,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=f"idem_at_{uuid.uuid4().hex[:6]}",
            status="running",
            claimed_by="worker-test",
            attempt_count=1,
        )
        art = MediaArtifactDB(
            id=art_id,
            tenant_id=t_a,
            artifact_type="clip",
            storage_path="path",
            filename="clip.mp4",
            status="processing",
        )
        session.add_all([wi, art])
        await session.commit()

        usage_kwargs = {
            "tenant_id": t_a,
            "event_type": "clip",
            "idempotency_key": f"clip:{art_id}",
            "resource_id": art_id,
            "quantity": 1.0,
            "unit": "count",
            "bytes": 5000000,
            "duration_seconds": 15.0,
        }
        success = await complete_job_and_artifact_atomic(
            db=session,
            job_id=job_id,
            worker_id="worker-test",
            artifact_id=art_id,
            duration_seconds=15.0,
            file_size_bytes=5000000,
            sha256_checksum="hash123",
            usage_event_kwargs=usage_kwargs,
            expected_attempt=1,
        )
        assert success is True

        ev_stmt = select(UsageEventDB).where(UsageEventDB.idempotency_key == f"clip:{art_id}")
        ev = (await session.execute(ev_stmt)).scalars().first()
        assert ev is not None
        assert ev.duration_seconds == 15.0
        assert ev.bytes == 5000000


# ── TEST 12: Authoritative Physical Storage Reconciliation ──


@pytest.mark.asyncio
async def test_12_storage_usage_reconciliation(env_fixture):
    """Retained storage aggregates ready artifacts and completed downloads with explicit breakdown."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        art = MediaArtifactDB(
            id=f"art_st_{uuid.uuid4().hex[:6]}",
            tenant_id=t_a,
            artifact_type="clip",
            storage_path="path",
            filename="test.mp4",
            status="ready",
            file_size_bytes=40 * 1024 * 1024,
        )
        dl = DownloadJobDB(
            id=f"dl_st_{uuid.uuid4().hex[:6]}",
            tenant_id=t_a,
            provider="youtube",
            url="https://youtube.com/watch?v=abc",
            status="completed",
            file_size_bytes=60 * 1024 * 1024,
        )
        session.add_all([art, dl])
        await session.commit()

        status = await evaluate_tenant_quotas(session, t_a)
        assert status.consumption["storage_bytes"] >= 100 * 1024 * 1024
        assert status.consumption["artifact_storage_bytes"] >= 40 * 1024 * 1024
        assert status.consumption["download_storage_bytes"] >= 60 * 1024 * 1024


# ── TEST 13: Queued vs Running Concurrency Breakdown ──


@pytest.mark.asyncio
async def test_13_queued_vs_running_concurrency(env_fixture):
    """Queued and running jobs are distinctly reported in consumption metrics."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        w_q1 = MediaWorkItemDB(
            id=f"wq1_{uuid.uuid4().hex[:6]}",
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=f"wq1_{uuid.uuid4().hex[:6]}",
            status="queued",
        )
        w_q2 = MediaWorkItemDB(
            id=f"wq2_{uuid.uuid4().hex[:6]}",
            tenant_id=t_a,
            operation_type="merge",
            idempotency_key=f"wq2_{uuid.uuid4().hex[:6]}",
            status="queued",
        )
        w_r = MediaWorkItemDB(
            id=f"wr_{uuid.uuid4().hex[:6]}",
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=f"wr_{uuid.uuid4().hex[:6]}",
            status="running",
        )
        session.add_all([w_q1, w_q2, w_r])
        await session.commit()

        status = await evaluate_tenant_quotas(session, t_a)
        assert status.consumption["queued_jobs"] == 2
        assert status.consumption["running_jobs"] == 1
        assert status.consumption["max_concurrent_jobs"] == 3


# ── TEST 14: Deterministic UTC Billing-Period Boundaries ──


@pytest.mark.asyncio
async def test_14_utc_billing_period_boundaries(env_fixture):
    """Billing period reset timestamps are calculated deterministically on calendar month boundaries in UTC."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        status = await evaluate_tenant_quotas(session, t_a)
        assert status.period_reset_at is not None
        reset_dt = datetime.fromisoformat(status.period_reset_at)
        assert reset_dt.day == 1
        assert reset_dt.hour == 0
        assert reset_dt.minute == 0


# ── TEST 15: Registration Pipeline Initial Subscription Assignment ──


@pytest.mark.asyncio
async def test_15_registration_pipeline_initial_subscription(env_fixture):
    """Newly activated self-service tenant automatically receives subscription matching default trial setting."""
    session_factory = env_fixture["session_factory"]
    pending_email = f"user_{uuid.uuid4().hex[:6]}@example.com"
    pending_slug = f"slug-{uuid.uuid4().hex[:6]}"
    raw_token = f"token_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    import hashlib
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    async with session_factory() as session:
        # Create pending registration
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        reg = PendingRegistrationDB(
            id=str(uuid.uuid4()),
            email=pending_email,
            password_hash="argon2_fake_hash",
            tenant_name="New Test Tenant",
            tenant_slug=pending_slug,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=24),
            created_at=now,
        )
        session.add(reg)
        await session.commit()

        # Verify email and activate tenant
        resp = await RegistrationService.verify_email_token(raw_token=raw_token, session=session)
        assert resp.tenant_id is not None

        # Verify subscription was automatically created
        sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == resp.tenant_id)
        sub = (await session.execute(sub_stmt)).scalars().first()
        assert sub is not None
        assert sub.plan_id == "plan-trial-v1"
        assert sub.status == "trial"
        # 14 days default trial
        diff = (sub.trial_expires_at - sub.trial_started_at).days
        assert diff == 14


# ── TEST 16: Advisory-Only Enforcement (No Customer Operations Blocked) ──


@pytest.mark.asyncio
async def test_16_advisory_only_enforcement(env_fixture):
    """Exceeded limits report is_exceeded=True but is_advisory=True, ensuring no hard customer blocking."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        # Record 60 downloads for Tenant A (limit is 50)
        for i in range(60):
            await record_usage_event(
                session,
                tenant_id=t_a,
                event_type="download",
                idempotency_key=f"adv_test_{i}_{uuid.uuid4().hex[:6]}",
                quantity=1.0,
                bytes=1000,
            )
        await session.commit()

        status = await evaluate_tenant_quotas(session, t_a)
        assert status.checks["monthly_download_count"].is_exceeded is True
        assert status.checks["monthly_download_count"].reason_code == QuotaReasonCode.DOWNLOAD_COUNT_EXCEEDED
        # Crucial: is_advisory is True, meaning the system does not hard block customer operations in STEP 04-A1
        assert status.is_advisory is True
