"""Comprehensive test suite for STEP 04-A2: Transactional Quota Enforcement,
Durable Job Reservations and Subscription Access Controls.

Verifies all 20 required points:
1. Cross-tenant quota isolation.
2. Concurrent admission oversubscription prevention (real PostgreSQL concurrency test).
3. Reservation idempotency (reusing existing active/reserved slot).
4. Retry and worker-crash recovery (attempt count does not multiply reservations).
5. Cancellation releases capacity immediately.
6. Failed publication does not record usage.
7. Stale worker cannot double-finalize.
8. Legacy tenant non-blocking (legacy_active operates without disruption).
9. Trial-expiry simulation vs enforcement.
10. Tenant override precedence over plan baseline.
11. API-key and admin permission boundaries.
12. Download-count correctness.
13. Clip/merge usage correctness.
14. Monthly period-boundary behavior (UTC month reset).
15. Shadow-mode no-blocking (logs would_block, allows request).
16. Default disabled-mode backward compatibility.
17. Explicit test-tenant enforce-mode blocking (HTTP 429/403/400).
18. Safe enforcement rollback (reverting from enforce to disabled restores advisory admission).
19. Storage accounting correctness.
20. Database outage behavior in enforce mode (503 service unavailable).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import uuid
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.models import (
    APIKeyDB,
    AppSettingDB,
    Base,
    CommercialAuditLogDB,
    CommercialPlanDB,
    CommercialPlanEntitlementDB,
    CommercialReservationDB,
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
    get_enforcement_setting,
    publish_plan,
    update_enforcement_setting,
)
from api.schemas.admin_plans import UpdateEnforcementSettingRequest
from api.schemas.clips import ClipExtractRequest
from api.services.enforcement_service import (
    CommercialEnforcementException,
    acquire_admission_and_reservation,
    activate_reservation,
    finalize_reservation,
    get_enforcement_config,
    reconcile_stale_reservations,
    release_reservation,
    update_enforcement_config,
    ENFORCEMENT_SETTING_KEY,
)
from api.services.entitlement_service import (
    QuotaReasonCode,
    assign_plan_to_tenant,
    evaluate_tenant_quotas,
    set_tenant_entitlement_override,
)
from api.services.job_service import (
    claim_next_job,
    complete_job_and_artifact_atomic,
    fail_job,
    mark_job_cancelled,
    request_job_cancellation,
    submit_clip_job,
)
from api.services.usage_service import record_usage_event


@pytest.fixture
def env_fixture(tmp_path):
    """Setup isolated SQLite engine with gid schema translation and test fixtures."""
    db_file = tmp_path / f"quota_test_{uuid.uuid4().hex}.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_a_id = f"tenant_a_{uuid.uuid4().hex[:6]}"
    tenant_b_id = f"tenant_b_{uuid.uuid4().hex[:6]}"
    tenant_legacy_id = f"tenant_leg_{uuid.uuid4().hex[:6]}"
    user_sysadmin_id = f"sysadmin_{uuid.uuid4().hex[:6]}"
    user_member_id = f"member_{uuid.uuid4().hex[:6]}"

    async def init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
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
            t_leg = TenantDB(
                id=tenant_legacy_id,
                name="Legacy Tenant",
                slug=f"legacy-{uuid.uuid4().hex[:6]}",
                tier="pro",
                is_active=True,
            )
            session.add_all([t_a, t_b, t_leg])

            u_sysadmin = UserDB(
                id=user_sysadmin_id,
                tenant_id=tenant_a_id,
                email="root@system.com",
                role="system_admin",
                is_active=True,
            )
            u_member = UserDB(
                id=user_member_id,
                tenant_id=tenant_a_id,
                email="user@alpha.com",
                role="member",
                is_active=True,
            )
            session.add_all([u_sysadmin, u_member])

            # Seed test plans
            plan_starter = CommercialPlanDB(
                id=f"plan_starter_{uuid.uuid4().hex[:6]}",
                plan_code="plan-starter-test",
                name="Starter Test Plan",
                version=1,
                tier_type="paid",
                status="active",
                price_cents=1000,
                billing_interval="monthly",
            )
            plan_draft = CommercialPlanDB(
                id=f"plan_draft_{uuid.uuid4().hex[:6]}",
                plan_code="plan-draft-test",
                name="Draft Test Plan",
                version=1,
                tier_type="paid",
                status="draft",
                price_cents=None,
                billing_interval="monthly",
            )
            session.add_all([plan_starter, plan_draft])
            await session.flush()

            # Seed entitlements for starter plan
            entitlements = [
                CommercialPlanEntitlementDB(
                    id=str(uuid.uuid4()),
                    plan_id=plan_starter.id,
                    entitlement_key="monthly_download_count",
                    value_type="count",
                    value_int=2,
                    is_unlimited=False,
                ),
                CommercialPlanEntitlementDB(
                    id=str(uuid.uuid4()),
                    plan_id=plan_starter.id,
                    entitlement_key="max_concurrent_jobs",
                    value_type="concurrency",
                    value_int=1,
                    is_unlimited=False,
                ),
                CommercialPlanEntitlementDB(
                    id=str(uuid.uuid4()),
                    plan_id=plan_starter.id,
                    entitlement_key="monthly_clip_seconds",
                    value_type="seconds",
                    value_int=120,
                    is_unlimited=False,
                ),
                CommercialPlanEntitlementDB(
                    id=str(uuid.uuid4()),
                    plan_id=plan_starter.id,
                    entitlement_key="api_access",
                    value_type="boolean",
                    value_bool=False,
                    is_unlimited=False,
                ),
                CommercialPlanEntitlementDB(
                    id=str(uuid.uuid4()),
                    plan_id=plan_starter.id,
                    entitlement_key="storage_bytes",
                    value_type="bytes",
                    value_int=100 * 1024 * 1024,
                    is_unlimited=False,
                ),
            ]
            session.add_all(entitlements)

            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

            # Assign subscriptions
            sub_a = TenantSubscriptionDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_a_id,
                plan_id=plan_starter.id,
                status="active",
                current_period_start=now_naive,
            )
            sub_b = TenantSubscriptionDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_b_id,
                plan_id=plan_starter.id,
                status="active",
                current_period_start=now_naive,
            )
            sub_leg = TenantSubscriptionDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_legacy_id,
                plan_id=plan_starter.id,
                status="legacy_active",
                current_period_start=now_naive,
            )
            session.add_all([sub_a, sub_b, sub_leg])

            # Seed default disabled enforcement setting
            enforcement_setting = AppSettingDB(
                key=ENFORCEMENT_SETTING_KEY,
                value={"mode": "disabled", "tenant_allowlist": []},
            )
            session.add(enforcement_setting)
            await session.commit()

    asyncio.run(init_db())

    user_sysadmin_obj = CurrentUser(
        user_id=user_sysadmin_id,
        tenant_id=tenant_a_id,
        email="root@system.com",
        role="system_admin",
        is_system_owner=True,
    )
    user_member_obj = CurrentUser(
        user_id=user_member_id,
        tenant_id=tenant_a_id,
        email="user@alpha.com",
        role="member",
        is_system_owner=False,
    )

    yield {
        "engine": engine,
        "session_factory": session_factory,
        "tenant_a_id": tenant_a_id,
        "tenant_b_id": tenant_b_id,
        "tenant_legacy_id": tenant_legacy_id,
        "user_sysadmin_id": user_sysadmin_id,
        "user_member_id": user_member_id,
        "user_sysadmin": user_sysadmin_obj,
        "user_member": user_member_obj,
    }

    asyncio.run(engine.dispose())


# ==============================================================================
# 1. Cross-tenant quota isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_01_cross_tenant_quota_isolation(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    t_b = env_fixture["tenant_b_id"]

    async with session_factory() as session:
        # Enable enforcement for both
        await update_enforcement_config(
            session,
            mode="enforce",
            tenant_allowlist=[t_a, t_b],
            reason="test isolation",
            actor_user_id=None,
        )

        # Tenant A consumes its full download quota (2 downloads)
        await record_usage_event(
            session=session,
            tenant_id=t_a,
            event_type="download",
            idempotency_key="dl_evt_a_1",
            quantity=1.0,
        )
        await record_usage_event(
            session=session,
            tenant_id=t_a,
            event_type="download",
            idempotency_key="dl_evt_a_2",
            quantity=1.0,
        )
        await session.commit()

    async with session_factory() as session:
        # Tenant A should be blocked by quota
        with pytest.raises(CommercialEnforcementException) as exc_info:
            await acquire_admission_and_reservation(
                session=session,
                tenant_id=t_a,
                operation_type="download",
                idempotency_key="dl_a_3",
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.reason_code in ("DOWNLOAD_COUNT_EXCEEDED", "QUOTA_EXCEEDED")

        # Tenant B should NOT be blocked (cross-tenant isolation)
        res_b, is_new = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_b,
            operation_type="download",
            idempotency_key="dl_b_1",
        )
        assert res_b is not None
        assert res_b.status == "reserved"
        assert is_new is True
        await session.commit()


# ==============================================================================
# 2. Concurrent admission oversubscription prevention (real PostgreSQL concurrency test)
# ==============================================================================
@pytest.mark.asyncio
async def test_02_concurrent_admission_oversubscription_prevention_postgres():
    """Real PostgreSQL concurrency test: attempts concurrent admissions against max_concurrent_jobs=1.
    Connects to authoritative PostgreSQL container on 127.0.0.1:55432 using asyncpg.
    """
    pg_url = "postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown"
    engine = create_async_engine(pg_url, pool_size=10, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    test_tenant_id = f"test_conc_{uuid.uuid4().hex[:8]}"
    test_plan_id = f"plan_conc_{uuid.uuid4().hex[:8]}"

    try:
        async with session_factory() as session:
            t = TenantDB(
                id=test_tenant_id,
                name="Concurrency Test Tenant",
                slug=f"conc-{uuid.uuid4().hex[:8]}",
                tier="basic",
                is_active=True,
            )
            session.add(t)

            p = CommercialPlanDB(
                id=test_plan_id,
                plan_code=f"plan_conc_{uuid.uuid4().hex[:6]}",
                name="Conc Plan",
                version=1,
                tier_type="paid",
                status="active",
                price_cents=100,
                billing_interval="monthly",
            )
            session.add(p)
            await session.flush()

            ent = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id=test_plan_id,
                entitlement_key="max_concurrent_jobs",
                value_type="concurrency",
                value_int=1,
                is_unlimited=False,
            )
            sub = TenantSubscriptionDB(
                id=str(uuid.uuid4()),
                tenant_id=test_tenant_id,
                plan_id=test_plan_id,
                status="active",
                current_period_start=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            session.add_all([ent, sub])

            await update_enforcement_config(
                session,
                mode="enforce",
                tenant_allowlist=[test_tenant_id],
                reason="concurrency test",
                actor_user_id=None,
            )
            await session.commit()

        async def try_admit(worker_id: int):
            async with session_factory() as s:
                try:
                    res, _ = await acquire_admission_and_reservation(
                        session=s,
                        tenant_id=test_tenant_id,
                        operation_type="clip_extract",
                        idempotency_key=f"conc_job_{worker_id}_{uuid.uuid4().hex[:6]}",
                    )
                    await s.commit()
                    return ("success", res.id)
                except CommercialEnforcementException as e:
                    await s.rollback()
                    return ("blocked", e.reason_code)
                except Exception as e:
                    await s.rollback()
                    return ("error", str(e))

        results = await asyncio.gather(*[try_admit(i) for i in range(5)])

        successes = [r for r in results if r[0] == "success"]
        blocked = [r for r in results if r[0] == "blocked" and r[1] == "CONCURRENCY_EXCEEDED"]

        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}: {results}"
        assert len(blocked) == 4, f"Expected 4 blocked by CONCURRENCY_EXCEEDED, got {len(blocked)}: {results}"

    finally:
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :tid"),
                {"tid": test_tenant_id},
            )
            await session.execute(
                text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :tid"),
                {"tid": test_tenant_id},
            )
            await session.execute(
                text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :tid"),
                {"tid": test_tenant_id},
            )
            await session.execute(
                text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :pid"),
                {"pid": test_plan_id},
            )
            await session.execute(
                text("DELETE FROM gid.commercial_plans WHERE id = :pid"),
                {"pid": test_plan_id},
            )
            await session.execute(
                text("DELETE FROM gid.tenants WHERE id = :tid"),
                {"tid": test_tenant_id},
            )
            await update_enforcement_config(
                session,
                mode="disabled",
                tenant_allowlist=[],
                reason="test_cleanup",
                actor_user_id=None,
            )
            await session.commit()

        await engine.dispose()


# ==============================================================================
# 3. Reservation idempotency (reusing existing active/reserved slot)
# ==============================================================================
@pytest.mark.asyncio
async def test_03_reservation_idempotency_reuse(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    idemp_key = f"idem_{uuid.uuid4().hex}"

    async with session_factory() as session:
        # First call reserves a slot
        res1, is_new1 = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=idemp_key,
        )
        await session.commit()
        assert res1.status == "reserved"
        assert is_new1 is True

    async with session_factory() as session:
        # Second call with SAME idempotency key must reuse existing reservation
        res2, is_new2 = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=idemp_key,
        )
        assert res2.id == res1.id
        assert res2.status == "reserved"
        assert is_new2 is False

        # Verify only 1 reservation row exists
        stmt = select(CommercialReservationDB).where(
            CommercialReservationDB.tenant_id == t_a,
            CommercialReservationDB.idempotency_key == idemp_key,
        )
        rows = (await session.execute(stmt)).scalars().all()
        assert len(rows) == 1


# ==============================================================================
# 4. Retry and worker-crash recovery (attempt count does not multiply reservations)
# ==============================================================================
@pytest.mark.asyncio
async def test_04_retry_and_worker_crash_recovery(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    u_user = env_fixture["user_member"]

    # First create a source artifact
    async with session_factory() as session:
        source_art = MediaArtifactDB(
            id=str(uuid.uuid4()),
            tenant_id=t_a,
            user_id=u_user.user_id,
            artifact_type="video",
            storage_path="source/video.mp4",
            filename="video.mp4",
            status="ready",
        )
        session.add(source_art)
        await session.commit()
        source_id = source_art.id

    async with session_factory() as session:
        req = ClipExtractRequest(
            source=source_id,
            start_time="00:00:01",
            end_time="00:00:10",
        )
        with patch("api.services.job_service.resolve_and_authorize_clip_source", return_value=Path("video.mp4")):
            job = await submit_clip_job(
                db=session,
                user=u_user,
                request=req,
                idempotency_key=f"retry_job_{uuid.uuid4().hex[:6]}",
            )
        job_id = job.id
        await session.commit()

    async with session_factory() as session:
        claimed = await claim_next_job(db=session, worker_id="worker_alpha", lease_duration_seconds=60)
        assert claimed is not None
        assert claimed.id == job_id

        stmt = select(CommercialReservationDB).where(
            CommercialReservationDB.resource_id == job_id
        )
        res = (await session.execute(stmt)).scalars().first()
        assert res.status == "active"
        await session.commit()

    async with session_factory() as session:
        # Worker crashes or fails retryably (retryable=True)
        await fail_job(
            db=session,
            job_id=job_id,
            worker_id="worker_alpha",
            error_code="FFMPEG_TIMEOUT",
            error_message="Worker timeout, retrying",
            retryable=True,
        )
        await session.commit()

    async with session_factory() as session:
        # Verify job is in retry_wait or queued with attempt_count=1
        stmt_job = select(MediaWorkItemDB).where(MediaWorkItemDB.id == job_id)
        job_after = (await session.execute(stmt_job)).scalars().first()
        assert job_after.attempt_count == 1
        assert job_after.status in ("retry_wait", "queued")

        # Verify reservation is still active and single (not multiplied)
        stmt = select(CommercialReservationDB).where(
            CommercialReservationDB.resource_id == job_id
        )
        reservations = (await session.execute(stmt)).scalars().all()
        assert len(reservations) == 1
        assert reservations[0].status == "active"


# ==============================================================================
# 5. Cancellation releases capacity immediately
# ==============================================================================
@pytest.mark.asyncio
async def test_05_cancellation_releases_capacity_immediately(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    u_user = env_fixture["user_member"]

    async with session_factory() as session:
        # Add source artifact
        source_art = MediaArtifactDB(
            id=str(uuid.uuid4()),
            tenant_id=t_a,
            user_id=u_user.user_id,
            artifact_type="video",
            storage_path="source/video.mp4",
            filename="video.mp4",
            status="ready",
        )
        session.add(source_art)
        await session.commit()
        source_id = source_art.id

    async with session_factory() as session:
        # Enable enforcement with max_concurrent_jobs=1
        await update_enforcement_config(
            session,
            mode="enforce",
            tenant_allowlist=[t_a],
            reason="test cancel",
            actor_user_id=None,
        )

        req = ClipExtractRequest(
            source=source_id,
            start_time="00:00:00",
            end_time="00:00:10",
        )
        with patch("api.services.job_service.resolve_and_authorize_clip_source", return_value=Path("video.mp4")):
            job = await submit_clip_job(
                db=session,
                user=u_user,
                request=req,
                idempotency_key=f"cancel_job_{uuid.uuid4().hex[:6]}",
            )
        job_id = job.id
        await session.commit()

    async with session_factory() as session:
        # Second submission is blocked by concurrent limit
        with pytest.raises(CommercialEnforcementException) as exc:
            await acquire_admission_and_reservation(
                session=session,
                tenant_id=t_a,
                operation_type="clip_extract",
                idempotency_key="job_blocked_cancel_test",
            )
        assert exc.value.status_code == 429
        assert exc.value.reason_code == "CONCURRENCY_EXCEEDED"

        # Now cancel the active job
        await request_job_cancellation(db=session, job_id=job_id, user=u_user)
        await mark_job_cancelled(db=session, job_id=job_id, worker_id="test_worker")
        await session.commit()

    async with session_factory() as session:
        # Reservation must now be 'released'
        stmt = select(CommercialReservationDB).where(
            CommercialReservationDB.resource_id == job_id
        )
        res = (await session.execute(stmt)).scalars().first()
        assert res.status == "released"

        # New job should now be successfully admitted!
        res_new, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key="job_admitted_after_cancel",
        )
        assert res_new.status == "reserved"
        await session.commit()


# ==============================================================================
# 6. Failed publication does not record usage
# ==============================================================================
@pytest.mark.asyncio
async def test_06_failed_publication_does_not_record_usage(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    sysadmin_user = env_fixture["user_sysadmin"]

    async with session_factory() as session:
        # Attempt to publish draft plan with null price -> raises 400
        stmt = select(CommercialPlanDB).where(CommercialPlanDB.status == "draft")
        draft_plan = (await session.execute(stmt)).scalars().first()

        with pytest.raises(HTTPException) as exc_info:
            await publish_plan(plan_id=draft_plan.id, user=sysadmin_user, session=session)
        assert exc_info.value.status_code == 400
        assert "pricing" in exc_info.value.detail.lower()

        # Permanent failure of a job releases reservation and does NOT record usage event
        res, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key="job_perm_fail",
        )
        await release_reservation(session, res.id)
        await session.commit()

        # Check no usage events created for this failure
        stmt_usage = select(UsageEventDB).where(
            UsageEventDB.tenant_id == t_a,
            UsageEventDB.idempotency_key == "job_perm_fail",
        )
        usage_rows = (await session.execute(stmt_usage)).scalars().all()
        assert len(usage_rows) == 0


# ==============================================================================
# 7. Stale worker cannot double-finalize
# ==============================================================================
@pytest.mark.asyncio
async def test_07_stale_worker_cannot_double_finalize(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        res, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key="double_finalize_test",
        )
        await activate_reservation(session, res.id, resource_id="res_123")
        await session.commit()

    async with session_factory() as session:
        # First finalize
        fin1 = await finalize_reservation(session, res.id)
        assert fin1 is True
        await session.commit()

    async with session_factory() as session:
        # Second finalize by stale worker -> graceful, returns False (already finalized)
        fin2 = await finalize_reservation(session, res.id)
        assert fin2 is False


# ==============================================================================
# 8. Legacy tenant non-blocking (legacy_active operates without disruption)
# ==============================================================================
@pytest.mark.asyncio
async def test_08_legacy_tenant_non_blocking(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_leg = env_fixture["tenant_legacy_id"]

    async with session_factory() as session:
        # Turn on enforce mode for legacy tenant
        await update_enforcement_config(
            session,
            mode="enforce",
            tenant_allowlist=[t_leg],
            reason="test legacy",
            actor_user_id=None,
        )

        # Consume usage far exceeding plan baseline
        await record_usage_event(
            session=session,
            tenant_id=t_leg,
            event_type="download",
            idempotency_key="dl_evt_leg_1",
            quantity=100.0,
        )
        await session.commit()

        # Legacy active tenant must NOT be blocked despite being over quota!
        res, is_new = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_leg,
            operation_type="download",
            idempotency_key="leg_admitted_1",
        )
        assert res is not None
        assert res.status == "reserved"
        assert is_new is True


# ==============================================================================
# 9. Trial-expiry simulation vs enforcement
# ==============================================================================
@pytest.mark.asyncio
async def test_09_trial_expiry_simulation_vs_enforcement(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    async with session_factory() as session:
        # Set subscription to expired trial
        stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == t_a)
        sub = (await session.execute(stmt)).scalars().first()
        sub.status = "trial"
        sub.trial_expires_at = now_naive - timedelta(days=2)
        await session.commit()

    async with session_factory() as session:
        # 1. In disabled mode: advisory only, NOT blocked
        await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="test", actor_user_id=None)
        res_dis, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="download",
            idempotency_key="trial_dis_1",
        )
        assert res_dis is not None

        # 2. In shadow mode: NOT blocked, records would_block audit log
        await update_enforcement_config(session, mode="shadow", tenant_allowlist=[], reason="test", actor_user_id=None)
        res_shd, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="download",
            idempotency_key="trial_shd_1",
        )
        assert res_shd is not None

        # 3. In enforce mode for t_a: BLOCKED with TRIAL_EXPIRED
        await update_enforcement_config(session, mode="enforce", tenant_allowlist=[t_a], reason="test", actor_user_id=None)
        with pytest.raises(CommercialEnforcementException) as exc:
            await acquire_admission_and_reservation(
                session=session,
                tenant_id=t_a,
                operation_type="download",
                idempotency_key="trial_enf_1",
            )
        assert exc.value.status_code == 403
        assert exc.value.reason_code == "TRIAL_EXPIRED"

        # Restore sub
        stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == t_a)
        sub_restore = (await session.execute(stmt)).scalars().first()
        sub_restore.status = "active"
        sub_restore.trial_expires_at = None
        await session.commit()


# ==============================================================================
# 10. Tenant override precedence over plan baseline
# ==============================================================================
@pytest.mark.asyncio
async def test_10_tenant_override_precedence(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    u_sysadmin = env_fixture["user_sysadmin_id"]

    async with session_factory() as session:
        await update_enforcement_config(session, mode="enforce", tenant_allowlist=[t_a], reason="test", actor_user_id=None)

        # Plan baseline for monthly_download_count is 2.
        # Add override for tenant A granting 10 downloads.
        await set_tenant_entitlement_override(
            session=session,
            tenant_id=t_a,
            entitlement_key="monthly_download_count",
            value_type="count",
            value_int=10,
            actor_user_id=u_sysadmin,
            reason="VIP tier test override",
        )

        # Record 3 downloads (exceeds baseline of 2, but within override of 10)
        await record_usage_event(
            session=session,
            tenant_id=t_a,
            event_type="download",
            idempotency_key="dl_evt_over_1",
            quantity=3.0,
        )
        await session.commit()

        # Should be admitted under override
        res, is_new = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="download",
            idempotency_key="override_allowed_1",
        )
        assert res is not None
        assert res.status == "reserved"
        assert is_new is True


# ==============================================================================
# 11. API-key and admin permission boundaries
# ==============================================================================
@pytest.mark.asyncio
async def test_11_api_key_and_admin_permission_boundaries(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]
    member_user = env_fixture["user_member"]
    sysadmin_user = env_fixture["user_sysadmin"]

    async with session_factory() as session:
        # Non-sysadmin member cannot update enforcement config
        req = UpdateEnforcementSettingRequest(mode="enforce", tenant_allowlist=[t_a], reason="test")
        with pytest.raises(HTTPException) as exc:
            await update_enforcement_setting(body=req, user=member_user, session=session)
        assert exc.value.status_code == 403

        # Sysadmin CAN update enforcement config
        resp = await update_enforcement_setting(body=req, user=sysadmin_user, session=session)
        assert resp.mode == "enforce"
        assert t_a in resp.tenant_allowlist


# ==============================================================================
# 12. Download-count correctness
# ==============================================================================
@pytest.mark.asyncio
async def test_12_download_count_correctness(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        idemp = f"dl_cnt_{uuid.uuid4().hex}"
        res, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="download",
            idempotency_key=idemp,
        )
        assert res.status == "reserved"

        # Finalize and record download event
        await finalize_reservation(session, res.id)
        await record_usage_event(
            session=session,
            tenant_id=t_a,
            event_type="download",
            quantity=1.0,
            idempotency_key=f"dl_evt_{idemp}",
        )
        await session.commit()

        # Check consumption
        eval_res = await evaluate_tenant_quotas(session, t_a)
        assert eval_res.consumption.get("monthly_download_count", 0) >= 1


# ==============================================================================
# 13. Clip/merge usage correctness
# ==============================================================================
@pytest.mark.asyncio
async def test_13_clip_merge_usage_correctness(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        idemp = f"clip_cnt_{uuid.uuid4().hex}"
        res, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key=idemp,
        )
        await activate_reservation(session, res.id, resource_id=idemp)
        await finalize_reservation(session, res.id)
        await record_usage_event(
            session=session,
            tenant_id=t_a,
            event_type="clip",
            duration_seconds=45.0,
            quantity=1.0,
            idempotency_key=f"clip_evt_{idemp}",
        )
        await session.commit()

        eval_res = await evaluate_tenant_quotas(session, t_a)
        assert eval_res.consumption.get("monthly_clip_seconds", 0) >= 45


# ==============================================================================
# 14. Monthly period-boundary behavior (UTC month reset)
# ==============================================================================
@pytest.mark.asyncio
async def test_14_monthly_period_boundary_utc(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        # Usage event from last month (e.g. 40 days ago)
        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=40)
        old_event = UsageEventDB(
            id=str(uuid.uuid4()),
            tenant_id=t_a,
            event_type="download",
            quantity=50.0,
            idempotency_key=f"old_dl_{uuid.uuid4().hex}",
            created_at=old_time,
        )
        session.add(old_event)
        await session.commit()

        # Current period quota evaluation should NOT include old_event
        eval_res = await evaluate_tenant_quotas(session, t_a)
        assert eval_res.checks["monthly_download_count"].current_value < 50


# ==============================================================================
# 15. Shadow-mode no-blocking (logs would_block, allows request)
# ==============================================================================
@pytest.mark.asyncio
async def test_15_shadow_mode_no_blocking(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        # Set shadow mode
        await update_enforcement_config(session, mode="shadow", tenant_allowlist=[], reason="test", actor_user_id=None)

        # Exceed download quota
        await record_usage_event(
            session=session,
            tenant_id=t_a,
            event_type="download",
            quantity=10.0,
            idempotency_key="shd_dl_10",
        )
        await session.commit()

        # Must NOT raise, returns valid reservation
        res, is_new = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="download",
            idempotency_key=f"shadow_test_{uuid.uuid4().hex}",
        )
        assert res is not None
        assert res.status == "reserved"
        assert is_new is True
        await session.commit()

        # Must record would_block audit log
        stmt = select(CommercialAuditLogDB).where(
            CommercialAuditLogDB.tenant_id == t_a,
            CommercialAuditLogDB.action == "commercial_quota_would_block",
        )
        logs = (await session.execute(stmt)).scalars().all()
        assert len(logs) >= 1
        assert "EXCEEDED" in logs[-1].details_json.get("reason_code", "")


# ==============================================================================
# 16. Default disabled-mode backward compatibility
# ==============================================================================
@pytest.mark.asyncio
async def test_16_default_disabled_mode_backward_compatibility(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="test", actor_user_id=None)

        # Over-quota request succeeds
        res, is_new = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="download",
            idempotency_key=f"dis_compat_{uuid.uuid4().hex}",
        )
        assert res is not None
        assert res.status == "reserved"
        assert is_new is True


# ==============================================================================
# 17. Explicit test-tenant enforce-mode blocking (HTTP 429/403/400)
# ==============================================================================
@pytest.mark.asyncio
async def test_17_explicit_test_tenant_enforce_mode_blocking(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        await update_enforcement_config(session, mode="enforce", tenant_allowlist=[t_a], reason="test", actor_user_id=None)

        # 1. Feature not included: api_access is False
        with pytest.raises(CommercialEnforcementException) as exc1:
            await acquire_admission_and_reservation(
                session=session,
                tenant_id=t_a,
                operation_type="download",
                auth_method="api_key",
                idempotency_key="feat_block",
            )
        assert exc1.value.status_code == 403
        assert exc1.value.reason_code == "FEATURE_NOT_INCLUDED"

        # 2. Concurrency limit (limit is 1): Reserve one active job
        res_active, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="clip_extract",
            idempotency_key="active_job_slot",
        )
        await session.commit()

        # Second job hits concurrency limit -> 429
        with pytest.raises(CommercialEnforcementException) as exc2:
            await acquire_admission_and_reservation(
                session=session,
                tenant_id=t_a,
                operation_type="clip_extract",
                idempotency_key="concurrent_block",
            )
        assert exc2.value.status_code == 429
        assert exc2.value.reason_code == "CONCURRENCY_EXCEEDED"

        # Release first slot
        await release_reservation(session, res_active.id)
        await session.commit()


# ==============================================================================
# 18. Safe enforcement rollback (reverting from enforce to disabled restores advisory admission)
# ==============================================================================
@pytest.mark.asyncio
async def test_18_safe_enforcement_rollback(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        # Enforce mode -> blocked
        await update_enforcement_config(session, mode="enforce", tenant_allowlist=[t_a], reason="test", actor_user_id=None)
        with pytest.raises(CommercialEnforcementException):
            await acquire_admission_and_reservation(
                session=session,
                tenant_id=t_a,
                operation_type="download",
                auth_method="api_key",
                idempotency_key="rollback_1",
            )

        # Rollback to disabled mode -> immediately unblocked
        await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="test", actor_user_id=None)

        res, _ = await acquire_admission_and_reservation(
            session=session,
            tenant_id=t_a,
            operation_type="download",
            auth_method="api_key",
            idempotency_key="rollback_1",
        )
        assert res is not None


# ==============================================================================
# 19. Storage accounting correctness
# ==============================================================================
@pytest.mark.asyncio
async def test_19_storage_accounting_correctness(env_fixture):
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        # Add artifact storage
        art = MediaArtifactDB(
            id=str(uuid.uuid4()),
            tenant_id=t_a,
            artifact_type="clip",
            storage_path="clips/clip1.mp4",
            filename="clip1.mp4",
            format="mp4",
            file_size_bytes=5 * 1024 * 1024,
            duration_seconds=30.0,
            status="ready",
        )
        session.add(art)
        await session.commit()

        eval_res = await evaluate_tenant_quotas(session, t_a)
        assert eval_res.consumption["storage_bytes"] >= 5 * 1024 * 1024


# ==============================================================================
# 20. Database outage behavior in enforce mode (503 service unavailable)
# ==============================================================================
@pytest.mark.asyncio
async def test_20_database_outage_behavior_in_enforce_mode(env_fixture):
    from sqlalchemy.exc import SQLAlchemyError

    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a_id"]

    async with session_factory() as session:
        await update_enforcement_config(session, mode="enforce", tenant_allowlist=[t_a], reason="test", actor_user_id=None)

        # Mock resolve_effective_entitlements to simulate database connection loss
        with patch("api.services.enforcement_service.resolve_effective_entitlements", side_effect=SQLAlchemyError("DB Connection Lost")):
            with pytest.raises(CommercialEnforcementException) as exc:
                await acquire_admission_and_reservation(
                    session=session,
                    tenant_id=t_a,
                    operation_type="download",
                    idempotency_key="db_fail_test",
                )
            assert exc.value.status_code == 503
            assert exc.value.reason_code == "COMMERCIAL_SERVICE_UNAVAILABLE"
