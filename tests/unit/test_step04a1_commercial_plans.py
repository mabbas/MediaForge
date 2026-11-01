"""Comprehensive test suite for STEP 04-A1: Commercial Plan Catalog, Tenant Entitlements,
Trial Configuration, and Accurate Usage Metering.

Verifies:
1. Cross-tenant entitlement isolation.
2. Ordinary users cannot change commercial limits.
3. API keys cannot elevate plan privileges.
4. Published plan-version stability and immutability.
5. Effective entitlement precedence (Override -> Plan Version -> System Default).
6. Trial expiry and administrative extension calculations (UTC).
7. Existing tenant backward compatibility.
8. Usage-event idempotency.
9. Download-byte measurement correctness.
10. Worker retry does not duplicate usage.
11. Cancelled/failed jobs do not record successful-output usage.
12. Storage-usage reconciliation.
13. Concurrent quota-evaluation consistency.
14. Zero-day trial behavior.
15. Read-only advisory simulation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.models import (
    APIKeyDB,
    Base,
    CommercialAuditLogDB,
    CommercialPlanDB,
    CommercialPlanEntitlementDB,
    DownloadJobDB,
    MediaArtifactDB,
    MediaWorkItemDB,
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
    list_plans,
    publish_plan,
    retire_plan,
    set_override,
)
from api.schemas.admin_plans import (
    AssignPlanRequest,
    EntitlementInput,
    EntitlementOverrideRequest,
    ExtendTrialRequest,
    PlanCreateRequest,
)
from api.services.entitlement_service import (
    CANONICAL_ENTITLEMENT_METRICS,
    QuotaReasonCode,
    assign_plan_to_tenant,
    evaluate_tenant_quotas,
    extend_tenant_trial,
    resolve_effective_entitlements,
    revoke_tenant_entitlement_override,
    set_tenant_entitlement_override,
    simulate_operation_admission,
)
from api.services.job_service import complete_job_and_artifact_atomic
from api.services.usage_service import record_usage_event


@pytest.fixture
def env_fixture(tmp_path):
    """Setup isolated SQLite engine with gid schema translation and test tenants."""
    db_file = tmp_path / "commercial_plans_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_a_id = f"tenant_a_{uuid.uuid4().hex[:6]}"
    tenant_b_id = f"tenant_b_{uuid.uuid4().hex[:6]}"
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
                tier="pro",
                is_active=True,
            )
            t_b = TenantDB(
                id=tenant_b_id,
                name="Tenant Beta",
                slug=f"beta-{uuid.uuid4().hex[:6]}",
                tier="basic",
                is_active=True,
            )
            session.add_all([t_a, t_b])

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

            # Seed standard plans
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            p_free = CommercialPlanDB(
                id="plan_free_test",
                plan_code="free",
                name="MediaForge Free",
                status="active",
                is_public=True,
                tier_type="free",
                version=1,
                price_cents=0,
                published_at=now,
            )
            p_creator = CommercialPlanDB(
                id="plan_creator_test",
                plan_code="creator_monthly",
                name="MediaForge Creator",
                status="active",
                is_public=True,
                tier_type="paid",
                version=1,
                price_cents=1900,
                published_at=now,
            )
            session.add_all([p_free, p_creator])

            # Entitlements for Free Plan: 50 downloads, 1 concurrency
            e_free_dl = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id="plan_free_test",
                entitlement_key="monthly_download_count",
                value_type="count",
                value_int=50,
            )
            e_free_c = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id="plan_free_test",
                entitlement_key="max_concurrent_jobs",
                value_type="concurrency",
                value_int=1,
            )
            # Entitlements for Creator Plan: 500 downloads, 2 concurrency
            e_cr_dl = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id="plan_creator_test",
                entitlement_key="monthly_download_count",
                value_type="count",
                value_int=500,
            )
            e_cr_c = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id="plan_creator_test",
                entitlement_key="max_concurrent_jobs",
                value_type="concurrency",
                value_int=2,
            )
            session.add_all([e_free_dl, e_free_c, e_cr_dl, e_cr_c])

            # Assign Tenant A -> Creator Plan; Tenant B -> Free Plan
            sub_a = TenantSubscriptionDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_a_id,
                plan_id="plan_creator_test",
                status="active",
                current_period_start=now,
            )
            sub_b = TenantSubscriptionDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_b_id,
                plan_id="plan_free_test",
                status="active",
                current_period_start=now,
            )
            session.add_all([sub_a, sub_b])

            await session.commit()

    asyncio.run(init_db())

    return {
        "engine": engine,
        "session_factory": session_factory,
        "tenant_a_id": tenant_a_id,
        "tenant_b_id": tenant_b_id,
        "user_admin_id": user_admin_id,
        "user_member_id": user_member_id,
        "user_sysadmin_id": user_sysadmin_id,
    }


# ── TEST 1: Cross-Tenant Entitlement Isolation ──


@pytest.mark.asyncio
async def test_01_cross_tenant_entitlement_isolation(env_fixture):
    """Tenant A's entitlements and usage do not leak into or alter Tenant B's limits."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    t_b = env_fixture["tenant_b_id"]

    async with session_factory() as session:
        status_a = await evaluate_tenant_quotas(session, t_a)
        status_b = await evaluate_tenant_quotas(session, t_b)

        assert status_a.tenant_id == t_a
        assert status_a.plan_code == "creator_monthly"
        assert status_a.checks["monthly_download_count"].limit_value == 500
        assert status_a.checks["max_concurrent_jobs"].limit_value == 2

        assert status_b.tenant_id == t_b
        assert status_b.plan_code == "free"
        assert status_b.checks["monthly_download_count"].limit_value == 50
        assert status_b.checks["max_concurrent_jobs"].limit_value == 1


# ── TEST 2: Ordinary Users Cannot Change Commercial Limits ──


@pytest.mark.asyncio
async def test_02_ordinary_users_cannot_change_commercial_limits(env_fixture):
    """Non-system-admins (members or tenant admins) cannot invoke administrative plan endpoints."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    # Member user principal
    member_user = CurrentUser(
        user_id=env_fixture["user_member_id"],
        tenant_id=t_a,
        role="member",
        is_system_owner=False,
    )
    # Tenant admin principal (not system_admin)
    tenant_admin_user = CurrentUser(
        user_id=env_fixture["user_admin_id"],
        tenant_id=t_a,
        role="admin",
        is_system_owner=False,
    )

    async with session_factory() as session:
        # Member attempt to create plan
        req = PlanCreateRequest(plan_code="exploit", name="Free Hack")
        with pytest.raises(HTTPException) as exc_info:
            await create_plan(body=req, user=member_user, session=session)
        assert exc_info.value.status_code == 403

        # Tenant admin attempt to set override
        ov_req = EntitlementOverrideRequest(
            entitlement_key="monthly_download_count",
            value_type="count",
            value_int=99999,
            reason="I am tenant admin",
        )
        with pytest.raises(HTTPException) as exc_info2:
            await set_override(tenant_id=t_a, body=ov_req, user=tenant_admin_user, session=session)
        assert exc_info2.value.status_code == 403


# ── TEST 3: API Keys Cannot Elevate Plan Privileges ──


@pytest.mark.asyncio
async def test_03_api_keys_cannot_elevate_plan_privileges(env_fixture):
    """API key principals fail closed when attempting administrative plan mutations."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    # Principal authenticated via API Key
    api_key_user = CurrentUser(
        user_id=f"service:{uuid.uuid4().hex[:8]}",
        tenant_id=t_a,
        auth_method=AuthMethod.API_KEY,
        api_key_id="key_12345",
        role="admin",
        permissions=["api:admin", "downloads:write"],
        is_system_owner=False,
    )

    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await list_plans(user=api_key_user, session=session)
        assert exc.value.status_code == 403


# ── TEST 4: Published Plan Version Stability ──


@pytest.mark.asyncio
async def test_04_published_plan_version_stability(env_fixture):
    """Published active plan versions cannot be republished or mutated silently."""
    session_factory = env_fixture["session_factory"]
    sysadmin = CurrentUser(
        user_id=env_fixture["user_sysadmin_id"],
        tenant_id=env_fixture["tenant_a_id"],
        role="system_admin",
        is_system_owner=True,
    )

    async with session_factory() as session:
        # Attempting to publish an already active plan raises 400
        with pytest.raises(HTTPException) as exc:
            await publish_plan(plan_id="plan_creator_test", user=sysadmin, session=session)
        assert exc.value.status_code == 400
        assert "already active" in exc.value.detail

        # Retiring plan marks it retired
        retired = await retire_plan(plan_id="plan_creator_test", user=sysadmin, session=session)
        assert retired.status == "retired"
        assert retired.retired_at is not None

        # Cannot assign a retired plan to a new tenant
        with pytest.raises(ValueError) as val_exc:
            await assign_plan_to_tenant(
                session,
                tenant_id=env_fixture["tenant_b_id"],
                plan_id="plan_creator_test",
                actor_user_id=sysadmin.user_id,
            )
        assert "is retired and cannot accept new assignments" in str(val_exc.value)


# ── TEST 5: Effective Entitlement Precedence ──


@pytest.mark.asyncio
async def test_05_effective_entitlement_precedence(env_fixture):
    """Precedence hierarchy: Override -> Assigned Plan Version -> System Default."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        # 1. Base assigned plan value is 500
        status_base = await evaluate_tenant_quotas(session, t_a)
        assert status_base.checks["monthly_download_count"].limit_value == 500
        assert status_base.effective_entitlements["monthly_download_count"]["source"] == "plan"

        # 2. Grant an override to 1000 with mandatory reason
        await set_tenant_entitlement_override(
            session,
            tenant_id=t_a,
            entitlement_key="monthly_download_count",
            value_type="count",
            value_int=1000,
            reason="VIP Customer Promotion",
            actor_user_id=env_fixture["user_sysadmin_id"],
        )
        await session.commit()

        # 3. Evaluate again: limit is now 1000 from override
        status_override = await evaluate_tenant_quotas(session, t_a)
        assert status_override.checks["monthly_download_count"].limit_value == 1000
        assert status_override.effective_entitlements["monthly_download_count"]["source"] == "override"
        assert status_override.effective_entitlements["monthly_download_count"]["override_reason"] == "VIP Customer Promotion"

        # 4. Revoke override: limit reverts back to plan (500)
        await revoke_tenant_entitlement_override(
            session,
            tenant_id=t_a,
            entitlement_key="monthly_download_count",
            reason="Promotion expired",
            actor_user_id=env_fixture["user_sysadmin_id"],
        )
        await session.commit()

        status_reverted = await evaluate_tenant_quotas(session, t_a)
        assert status_reverted.checks["monthly_download_count"].limit_value == 500
        assert status_reverted.effective_entitlements["monthly_download_count"]["source"] == "plan"


# ── TEST 6: Trial Expiry and Administrative Extension Calculations ──


@pytest.mark.asyncio
async def test_06_trial_expiry_and_administrative_extension_calculations(env_fixture):
    """Trial timestamps are calculated in UTC; administrative extension strictly extends expiration date."""
    session_factory = env_fixture["session_factory"]
    t_b = env_fixture["tenant_b_id"]
    sysadmin_id = env_fixture["user_sysadmin_id"]

    async with session_factory() as session:
        # Assign 14-day trial to Tenant B
        sub = await assign_plan_to_tenant(
            session,
            tenant_id=t_b,
            plan_id="plan_free_test",
            status="trial",
            trial_days=14,
            actor_user_id=sysadmin_id,
            reason="Initial 14-day onboarding trial",
        )
        await session.commit()

        assert sub.status == "trial"
        assert sub.trial_started_at is not None
        assert sub.trial_expires_at is not None
        expected_diff = (sub.trial_expires_at - sub.trial_started_at).days
        assert expected_diff == 14

        orig_expiry = sub.trial_expires_at

        # Extend trial by 7 days
        extended_sub = await extend_tenant_trial(
            session,
            tenant_id=t_b,
            additional_days=7,
            reason="Customer requested evaluation extension for security review",
            actor_user_id=sysadmin_id,
        )
        await session.commit()

        assert extended_sub.trial_expires_at == orig_expiry + timedelta(days=7)

        # Audit log verification
        audit_stmt = select(CommercialAuditLogDB).where(
            CommercialAuditLogDB.tenant_id == t_b,
            CommercialAuditLogDB.action == "trial_extended",
        )
        audit_row = (await session.execute(audit_stmt)).scalars().first()
        assert audit_row is not None
        assert audit_row.reason == "Customer requested evaluation extension for security review"


# ── TEST 7: Existing Tenant Backward Compatibility ──


@pytest.mark.asyncio
async def test_07_existing_tenant_backward_compatibility(env_fixture):
    """An unassigned or legacy tenant safely falls back to default entitlements without blocking."""
    session_factory = env_fixture["session_factory"]
    legacy_tenant_id = f"legacy_{uuid.uuid4().hex[:6]}"

    async with session_factory() as session:
        t_legacy = TenantDB(
            id=legacy_tenant_id,
            name="Legacy Unassigned Tenant",
            slug=f"legacy-{uuid.uuid4().hex[:6]}",
            tier="basic",
            is_active=True,
        )
        session.add(t_legacy)
        await session.commit()

        # Evaluate quota for legacy tenant with no subscription row
        status = await evaluate_tenant_quotas(session, legacy_tenant_id)
        assert status.tenant_id == legacy_tenant_id
        assert status.plan_code == "default_free"
        assert status.subscription_status == "unassigned"
        assert status.is_advisory is True
        # Canonical defaults resolved
        assert status.checks["monthly_download_count"].limit_value == 50
        assert status.checks["desktop_access"].allowed is True


# ── TEST 8: Usage-Event Idempotency ──


@pytest.mark.asyncio
async def test_08_usage_event_idempotency(env_fixture):
    """Replaying identical usage event parameters returns existing event without double-counting."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    clean_key = f"download:test_job_{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        ev1, is_new1 = await record_usage_event(
            session,
            tenant_id=t_a,
            event_type="download",
            idempotency_key=clean_key,
            quantity=1.0,
            bytes=50000000,
        )
        await session.commit()
        assert is_new1 is True

        ev2, is_new2 = await record_usage_event(
            session,
            tenant_id=t_a,
            event_type="download",
            idempotency_key=clean_key,
            quantity=1.0,
            bytes=50000000,
        )
        assert is_new2 is False
        assert ev1.id == ev2.id

        # Verify count in database is exactly 1
        count_stmt = select(UsageEventDB).where(UsageEventDB.idempotency_key == clean_key)
        rows = (await session.execute(count_stmt)).scalars().all()
        assert len(rows) == 1


# ── TEST 9: Download-Byte Measurement Correctness ──


@pytest.mark.asyncio
async def test_09_download_byte_measurement_correctness(env_fixture):
    """Recorded download bytes accurately increment consumption in commercial quota checks."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    job_id = f"job_dl_{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        # Record 100 MB download
        file_bytes = 100 * 1024 * 1024
        await record_usage_event(
            session,
            tenant_id=t_a,
            event_type="download",
            idempotency_key=f"download:{job_id}",
            resource_id=job_id,
            quantity=1.0,
            bytes=file_bytes,
        )
        await session.commit()

        status = await evaluate_tenant_quotas(session, t_a)
        assert status.consumption["monthly_download_bytes"] >= file_bytes
        assert status.consumption["monthly_download_count"] >= 1


# ── TEST 10: Worker Retry Does Not Duplicate Usage ──


@pytest.mark.asyncio
async def test_10_worker_retry_does_not_duplicate_usage(env_fixture):
    """Failed retry attempts do not commit usage events; only final successful publication commits."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    job_id = f"work_item_{uuid.uuid4().hex[:8]}"
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    idem_key = f"clip:{art_id}"

    async with session_factory() as session:
        # Create work item & artifact
        wi = MediaWorkItemDB(
            id=job_id,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=f"idem_wi_{uuid.uuid4().hex[:6]}",
            status="running",
            claimed_by="worker-1",
            attempt_count=1,
        )
        art = MediaArtifactDB(
            id=art_id,
            tenant_id=t_a,
            artifact_type="clip",
            storage_path=f"{t_a}/clips/{art_id}.mp4",
            filename=f"{art_id}.mp4",
            status="processing",
        )
        session.add_all([wi, art])
        await session.commit()

        # Attempt 1 fails: no usage event should be added
        # Simulated retry: attempt count increments
        wi.attempt_count = 2
        await session.commit()

        check_stmt = select(UsageEventDB).where(UsageEventDB.idempotency_key == idem_key)
        assert (await session.execute(check_stmt)).scalars().first() is None

        # Final successful completion: atomically commits work item + artifact + usage
        usage_kwargs = {
            "tenant_id": t_a,
            "event_type": "clip",
            "idempotency_key": idem_key,
            "resource_id": art_id,
            "quantity": 1.0,
            "unit": "count",
            "bytes": 2048000,
            "duration_seconds": 30.0,
        }
        success = await complete_job_and_artifact_atomic(
            db=session,
            job_id=job_id,
            worker_id="worker-1",
            artifact_id=art_id,
            duration_seconds=30.0,
            file_size_bytes=2048000,
            sha256_checksum="abc123hash",
            usage_event_kwargs=usage_kwargs,
            expected_attempt=2,
        )
        assert success is True

        # Exactly 1 usage event exists
        events = (await session.execute(check_stmt)).scalars().all()
        assert len(events) == 1
        assert events[0].bytes == 2048000


# ── TEST 11: Cancelled/Failed Jobs Do Not Record Usage ──


@pytest.mark.asyncio
async def test_11_cancelled_or_failed_jobs_do_not_record_successful_usage(env_fixture):
    """Cancelled work items do not commit successful artifact or usage events."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    job_id = f"work_item_cancel_{uuid.uuid4().hex[:8]}"
    art_id = f"art_cancel_{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        wi = MediaWorkItemDB(
            id=job_id,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=f"idem_wi_{uuid.uuid4().hex[:6]}",
            status="running",
            claimed_by="worker-1",
            cancellation_requested_at=datetime.utcnow(),
        )
        art = MediaArtifactDB(
            id=art_id,
            tenant_id=t_a,
            artifact_type="clip",
            storage_path=f"{t_a}/clips/{art_id}.mp4",
            filename=f"{art_id}.mp4",
            status="processing",
        )
        session.add_all([wi, art])
        await session.commit()

        # Worker attempts to complete cancelled job
        usage_kwargs = {
            "tenant_id": t_a,
            "event_type": "clip",
            "idempotency_key": f"clip:{art_id}",
            "resource_id": art_id,
            "quantity": 1.0,
            "unit": "count",
            "bytes": 1000,
            "duration_seconds": 10.0,
        }
        success = await complete_job_and_artifact_atomic(
            db=session,
            job_id=job_id,
            worker_id="worker-1",
            artifact_id=art_id,
            duration_seconds=10.0,
            file_size_bytes=1000,
            sha256_checksum="abc",
            usage_event_kwargs=usage_kwargs,
        )
        assert success is False

        # No usage event committed
        ev_stmt = select(UsageEventDB).where(UsageEventDB.idempotency_key == f"clip:{art_id}")
        assert (await session.execute(ev_stmt)).scalars().first() is None


# ── TEST 12: Storage-Usage Reconciliation ──


@pytest.mark.asyncio
async def test_12_storage_usage_reconciliation(env_fixture):
    """Authoritative storage is computed from sum of ready artifacts and completed downloads."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        # Add ready artifact of 25 MB
        art = MediaArtifactDB(
            id=f"art_st_{uuid.uuid4().hex[:8]}",
            tenant_id=t_a,
            artifact_type="merge",
            storage_path="path",
            filename="merged.mp4",
            status="ready",
            file_size_bytes=25 * 1024 * 1024,
        )
        # Add completed download of 75 MB
        dl = DownloadJobDB(
            id=f"dl_st_{uuid.uuid4().hex[:8]}",
            tenant_id=t_a,
            provider="youtube",
            url="https://youtube.com/watch?v=123",
            status="completed",
            file_size_bytes=75 * 1024 * 1024,
        )
        session.add_all([art, dl])
        await session.commit()

        status = await evaluate_tenant_quotas(session, t_a)
        expected_bytes = 100 * 1024 * 1024  # 100 MB
        assert status.consumption["storage_bytes"] >= expected_bytes


# ── TEST 13: Concurrent Quota-Evaluation Consistency ──


@pytest.mark.asyncio
async def test_13_concurrent_quota_evaluation_consistency(env_fixture):
    """Running & queued work items count towards active concurrency; finished items do not."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        # 1 queued item, 1 running item, 1 succeeded item
        w1 = MediaWorkItemDB(
            id=f"w1_{uuid.uuid4().hex[:6]}",
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=f"w1_{uuid.uuid4().hex[:6]}",
            status="queued",
        )
        w2 = MediaWorkItemDB(
            id=f"w2_{uuid.uuid4().hex[:6]}",
            tenant_id=t_a,
            operation_type="merge",
            idempotency_key=f"w2_{uuid.uuid4().hex[:6]}",
            status="running",
        )
        w3 = MediaWorkItemDB(
            id=f"w3_{uuid.uuid4().hex[:6]}",
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=f"w3_{uuid.uuid4().hex[:6]}",
            status="succeeded",
        )
        session.add_all([w1, w2, w3])
        await session.commit()

        status = await evaluate_tenant_quotas(session, t_a)
        # Concurrency should be exactly 2 (w1 + w2)
        assert status.consumption["max_concurrent_jobs"] == 2
        # Creator plan limit is 2 -> current_value is 2, limit is 2
        assert status.checks["max_concurrent_jobs"].current_value == 2.0


# ── TEST 14: Zero-Day Trial Behavior ──


@pytest.mark.asyncio
async def test_14_zero_day_trial_behavior(env_fixture):
    """A 0-day trial plan starts expired immediately without granting unauthorized periods."""
    session_factory = env_fixture["session_factory"]
    t_b = env_fixture["tenant_b_id"]

    async with session_factory() as session:
        await assign_plan_to_tenant(
            session,
            tenant_id=t_b,
            plan_id="plan_free_test",
            status="trial",
            trial_days=0,
            actor_user_id=env_fixture["user_sysadmin_id"],
            reason="Zero day trial test",
        )
        await session.commit()

        status = await evaluate_tenant_quotas(session, t_b)
        assert status.is_trial is True
        assert status.subscription_status == "expired"
        assert status.trial_days_remaining == 0


# ── TEST 15: Read-Only Advisory Simulation ──


@pytest.mark.asyncio
async def test_15_read_only_advisory_simulation(env_fixture):
    """Simulating an operation evaluates prospective quotas without altering state or blocking."""
    session_factory = env_fixture["session_factory"]
    t_b = env_fixture["tenant_b_id"]  # Free plan has 50 download limit

    async with session_factory() as session:
        # Simulate download when under limit
        sim_ok = await simulate_operation_admission(
            session,
            tenant_id=t_b,
            operation_type="download",
            estimated_bytes=10000000,
        )
        assert sim_ok["admissible"] is True
        assert sim_ok["is_advisory"] is True
        assert sim_ok["reason_codes"] == [QuotaReasonCode.QUOTA_OK.value]

        # Record 50 downloads to exhaust quota
        for i in range(50):
            await record_usage_event(
                session,
                tenant_id=t_b,
                event_type="download",
                idempotency_key=f"dl_sim_{i}_{uuid.uuid4().hex[:6]}",
                quantity=1.0,
                bytes=1000,
            )
        await session.commit()

        # Simulate again: should flag DOWNLOAD_COUNT_EXCEEDED
        sim_exceeded = await simulate_operation_admission(
            session,
            tenant_id=t_b,
            operation_type="download",
        )
        assert sim_exceeded["admissible"] is False
        assert sim_exceeded["is_advisory"] is True
        assert QuotaReasonCode.DOWNLOAD_COUNT_EXCEEDED.value in sim_exceeded["reason_codes"]
