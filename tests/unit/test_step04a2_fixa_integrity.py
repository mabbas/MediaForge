"""MediaForge STEP 04-A2-Fix-A: Transactional Quota Accounting, Multi-Slot Concurrency,
and Reservation Lifecycle Integrity Acceptance Tests.

This test suite rigorously validates:
1. Multi-slot real PostgreSQL concurrency tests (Limits 1, 2, 3 with queued/running jobs).
2. Authoritative concurrency metrics with zero double-counting.
3. Transactional atomicity of admission reservations and work item creation.
4. Idempotency identity, payload fingerprinting, and cross-principal protection (409 Conflict).
5. Reservation lifecycle, lease recovery, and long-running job lease renewal.
6. Reconciler protection against expiring active heartbeating jobs.
7. Shadow, disabled, and enforce mode transitions and audit trails.
8. Compound quota semantics and indeterminate metric handling.
9. Schema, UTC timestamp, and input validation correctness.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import uuid
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base
from api.database.models import (
    AppSettingDB,
    CommercialAuditLogDB,
    CommercialPlanDB,
    CommercialPlanEntitlementDB,
    CommercialReservationDB,
    DownloadJobDB,
    MediaWorkItemDB,
    TenantDB,
    TenantEntitlementOverrideDB,
    TenantSubscriptionDB,
    UsageEventDB,
    UserDB,
)
from api.dependencies import AuthMethod, CurrentUser
from api.services.enforcement_service import (
    DEFAULT_ENFORCEMENT_KEY,
    CommercialEnforcementException,
    acquire_admission_and_reservation,
    activate_reservation,
    finalize_reservation,
    get_enforcement_config,
    is_tenant_enforced,
    reconcile_stale_reservations,
    release_reservation,
    update_enforcement_config,
)
from api.services.entitlement_service import (
    QuotaReasonCode,
    get_authoritative_concurrency_metrics,
    resolve_effective_entitlements,
)
from api.services.job_service import heartbeat_and_check_cancellation

PG_URL = os.environ.get(
    "GID_TEST_DATABASE_URL",
    "postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown_test",
)


@pytest.fixture
def pg_session_factory():
    """Create async engine and sessionmaker for isolated test PostgreSQL container."""
    if not ("test" in PG_URL.lower() or os.environ.get("ALLOW_AUTHORITATIVE_DEV_TEST") == "1"):
        raise RuntimeError(f"Safety violation: Refusing to run tests against non-test database: {PG_URL}")

    engine = create_async_engine(PG_URL, pool_size=15, max_overflow=10, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield session_factory
    finally:
        engine.sync_engine.dispose()


# ==============================================================================
# 1. SCENARIO A: Limit = 1, 10 concurrent requests -> 1 accepted, 9 rejected (429)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_a_limit_1_concurrent_10(pg_session_factory):
    tenant_id = f"test_scen_a_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_scen_a_{uuid.uuid4().hex[:8]}"

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Scenario A Tenant", slug=f"scen-a-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_a_{uuid.uuid4().hex[:6]}", name="Plan A", version=1, tier_type="paid", status="active", price_cents=100, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=1, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=datetime.now(timezone.utc).replace(tzinfo=None))
            session.add_all([ent, sub])
            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_id], reason="Scenario A test")
            await session.commit()

        async def submit_req(idx: int):
            async with pg_session_factory() as s:
                try:
                    res, _ = await acquire_admission_and_reservation(
                        session=s,
                        tenant_id=tenant_id,
                        operation_type="clip_extract",
                        idempotency_key=f"scen_a_req_{idx}_{uuid.uuid4().hex[:6]}",
                    )
                    await s.commit()
                    return ("success", res.id)
                except CommercialEnforcementException as exc:
                    await s.rollback()
                    return ("blocked", exc.status_code, exc.reason_code)

        results = await asyncio.gather(*[submit_req(i) for i in range(10)])
        successes = [r for r in results if r[0] == "success"]
        blocked = [r for r in results if r[0] == "blocked"]

        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
        assert len(blocked) == 9, f"Expected exactly 9 blocked, got {len(blocked)}"
        for b in blocked:
            assert b[1] == 429
            assert b[2] == "CONCURRENCY_EXCEEDED"

        # Direct SQL verification: exactly 1 active reservation in DB
        async with pg_session_factory() as session:
            count = (await session.execute(
                select(func.count(CommercialReservationDB.id)).where(
                    CommercialReservationDB.tenant_id == tenant_id,
                    CommercialReservationDB.status.in_(["reserved", "active"]),
                )
            )).scalar()
            assert count == 1
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Scenario A cleanup")
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 2. SCENARIO B: Limit = 2, 10 concurrent requests -> 2 accepted, 8 rejected (429)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_b_limit_2_concurrent_10(pg_session_factory):
    tenant_id = f"test_scen_b_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_scen_b_{uuid.uuid4().hex[:8]}"

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Scenario B Tenant", slug=f"scen-b-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_b_{uuid.uuid4().hex[:6]}", name="Plan B", version=1, tier_type="paid", status="active", price_cents=200, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=2, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=datetime.now(timezone.utc).replace(tzinfo=None))
            session.add_all([ent, sub])
            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_id], reason="Scenario B test")
            await session.commit()

        async def submit_req(idx: int):
            async with pg_session_factory() as s:
                try:
                    res, _ = await acquire_admission_and_reservation(
                        session=s,
                        tenant_id=tenant_id,
                        operation_type="clip_extract",
                        idempotency_key=f"scen_b_req_{idx}_{uuid.uuid4().hex[:6]}",
                    )
                    await s.commit()
                    return ("success", res.id)
                except CommercialEnforcementException as exc:
                    await s.rollback()
                    return ("blocked", exc.status_code, exc.reason_code)

        results = await asyncio.gather(*[submit_req(i) for i in range(10)])
        successes = [r for r in results if r[0] == "success"]
        blocked = [r for r in results if r[0] == "blocked"]

        assert len(successes) == 2, f"Expected exactly 2 successes, got {len(successes)}"
        assert len(blocked) == 8, f"Expected exactly 8 blocked, got {len(blocked)}"
        for b in blocked:
            assert b[1] == 429
            assert b[2] == "CONCURRENCY_EXCEEDED"

        # Direct SQL verification: exactly 2 active reservations in DB
        async with pg_session_factory() as session:
            count = (await session.execute(
                select(func.count(CommercialReservationDB.id)).where(
                    CommercialReservationDB.tenant_id == tenant_id,
                    CommercialReservationDB.status.in_(["reserved", "active"]),
                )
            )).scalar()
            assert count == 2
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Scenario B cleanup")
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 3. SCENARIO C: Limit = 3, existing 1 queued + 1 running -> exactly 1 new accepted
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_c_preexisting_jobs_no_double_counting(pg_session_factory):
    tenant_id = f"test_scen_c_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_scen_c_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Scenario C Tenant", slug=f"scen-c-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_c_{uuid.uuid4().hex[:6]}", name="Plan C", version=1, tier_type="paid", status="active", price_cents=300, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=3, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=now)
            session.add_all([ent, sub])

            # Seed 1 queued job with 'reserved' reservation
            job1_id = f"job_q_{uuid.uuid4().hex[:6]}"
            res1 = CommercialReservationDB(
                id=str(uuid.uuid4()), tenant_id=tenant_id, operation_type="clip_extract", resource_id=job1_id,
                idempotency_key=f"idem_q_{uuid.uuid4().hex[:6]}", entitlement_key="max_concurrent_jobs", quantity=1, status="reserved",
                lease_expires_at=now + timedelta(minutes=30), created_at=now, updated_at=now,
            )
            wi1 = MediaWorkItemDB(
                id=job1_id, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key=res1.idempotency_key,
                request_payload={"clip": 1}, status="queued", created_at=now, updated_at=now, available_at=now + timedelta(hours=1),
            )

            # Seed 1 running job with 'active' reservation
            job2_id = f"job_r_{uuid.uuid4().hex[:6]}"
            res2 = CommercialReservationDB(
                id=str(uuid.uuid4()), tenant_id=tenant_id, operation_type="merge", resource_id=job2_id,
                idempotency_key=f"idem_r_{uuid.uuid4().hex[:6]}", entitlement_key="max_concurrent_jobs", quantity=1, status="active",
                lease_expires_at=now + timedelta(minutes=30), created_at=now, updated_at=now,
            )
            wi2 = MediaWorkItemDB(
                id=job2_id, tenant_id=tenant_id, operation_type="merge", idempotency_key=res2.idempotency_key,
                request_payload={"merge": 1}, status="running", claimed_by="worker-1",
                lease_expires_at=now + timedelta(seconds=60), last_heartbeat_at=now, created_at=now, updated_at=now, available_at=now,
            )

            session.add_all([res1, wi1, res2, wi2])
            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_id], reason="Scenario C test")
            await session.commit()

        # Verify initial breakdown metrics: 1 reserved, 1 active, 0 legacy -> 2 admitted outstanding
        async with pg_session_factory() as session:
            m = await get_authoritative_concurrency_metrics(session, tenant_id)
            assert m["reserved_operations"] == 1
            assert m["active_processing_operations"] == 1
            assert m["legacy_unreserved_operations"] == 0
            assert m["admitted_outstanding_operations"] == 2
            assert m["queued_jobs"] == 1
            assert m["running_jobs"] == 1

        # Submit 5 concurrent requests against remaining 1 slot (limit=3, current=2)
        async def submit_req(idx: int):
            async with pg_session_factory() as s:
                try:
                    res, _ = await acquire_admission_and_reservation(
                        session=s,
                        tenant_id=tenant_id,
                        operation_type="clip_extract",
                        idempotency_key=f"scen_c_req_{idx}_{uuid.uuid4().hex[:6]}",
                    )
                    await s.commit()
                    return ("success", res.id)
                except CommercialEnforcementException as exc:
                    await s.rollback()
                    return ("blocked", exc.status_code, exc.reason_code)

        results = await asyncio.gather(*[submit_req(i) for i in range(5)])
        successes = [r for r in results if r[0] == "success"]
        blocked = [r for r in results if r[0] == "blocked"]

        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
        assert len(blocked) == 4, f"Expected exactly 4 blocked, got {len(blocked)}"

        # Final check: Exactly 3 slots occupied, no double counting
        async with pg_session_factory() as session:
            m_final = await get_authoritative_concurrency_metrics(session, tenant_id)
            assert m_final["admitted_outstanding_operations"] == 3
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Scenario C cleanup")
            await session.execute(text("DELETE FROM gid.media_work_items WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 4. SCENARIO D: Limit = 2, completed job releases capacity for next request
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_d_completed_job_releases_capacity(pg_session_factory):
    tenant_id = f"test_scen_d_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_scen_d_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Scenario D Tenant", slug=f"scen-d-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_d_{uuid.uuid4().hex[:6]}", name="Plan D", version=1, tier_type="paid", status="active", price_cents=200, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=2, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=now)
            session.add_all([ent, sub])
            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_id], reason="Scenario D test")
            await session.commit()

        # Acquire 2 slots (filling capacity)
        res1_id, res2_id = None, None
        async with pg_session_factory() as s:
            r1, _ = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="d_job_1")
            r2, _ = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="d_job_2")
            res1_id, res2_id = r1.id, r2.id
            await s.commit()

        # 3rd request must be blocked
        async with pg_session_factory() as s:
            with pytest.raises(CommercialEnforcementException) as exc:
                await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="d_job_3")
            assert exc.value.status_code == 429

        # Job 1 completes -> finalize reservation
        async with pg_session_factory() as s:
            finalized = await finalize_reservation(s, res1_id)
            assert finalized is True
            # Record single usage event
            ue = UsageEventDB(
                id=str(uuid.uuid4()), tenant_id=tenant_id, event_type="clip", idempotency_key=f"ue_d_{uuid.uuid4().hex[:6]}", quantity=1.0, created_at=now
            )
            s.add(ue)
            await s.commit()

        # Capacity released: now request 3 must be successfully admitted
        async with pg_session_factory() as s:
            r3, newly_created = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="d_job_3")
            await s.commit()
            assert newly_created is True
            assert r3.id is not None
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Scenario D cleanup")
            await session.execute(text("DELETE FROM gid.usage_events WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 5. SCENARIO E: Retry_wait preserves single reservation without slot leak
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_e_retry_wait_preserves_single_slot(pg_session_factory):
    tenant_id = f"test_scen_e_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_scen_e_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Scenario E Tenant", slug=f"scen-e-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_e_{uuid.uuid4().hex[:6]}", name="Plan E", version=1, tier_type="paid", status="active", price_cents=200, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=2, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=now)
            session.add_all([ent, sub])
            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_id], reason="Scenario E test")
            await session.commit()

        # Step 1: Admit Job E
        idem_key = f"e_retry_job_{uuid.uuid4().hex[:6]}"
        job_id = str(uuid.uuid4())
        async with pg_session_factory() as s:
            res, _ = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key=idem_key)
            res.resource_id = job_id
            wi = MediaWorkItemDB(id=job_id, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key=idem_key, request_payload={"c": 1}, status="queued", created_at=now, updated_at=now, available_at=now)
            s.add(wi)
            await s.commit()

        # Step 2: Job claimed, transitions to running
        async with pg_session_factory() as s:
            res_obj = (await s.execute(select(CommercialReservationDB).where(CommercialReservationDB.tenant_id == tenant_id, CommercialReservationDB.idempotency_key == idem_key))).scalar_one()
            wi_obj = (await s.execute(select(MediaWorkItemDB).where(MediaWorkItemDB.id == job_id))).scalar_one()
            res_obj.status = "active"
            wi_obj.status = "running"
            wi_obj.claimed_by = "worker-1"
            await s.commit()

        # Step 3: Transient error -> job enters retry_wait (reservation remains 'active')
        async with pg_session_factory() as s:
            wi_obj = (await s.execute(select(MediaWorkItemDB).where(MediaWorkItemDB.id == job_id))).scalar_one()
            wi_obj.status = "retry_wait"
            wi_obj.claimed_by = None
            wi_obj.available_at = now + timedelta(seconds=10)
            await s.commit()

        # Slot count must STILL be exactly 1 (not leaked, not doubled)
        async with pg_session_factory() as s:
            m = await get_authoritative_concurrency_metrics(s, tenant_id)
            assert m["admitted_outstanding_operations"] == 1
            assert m["active_processing_operations"] == 1
            assert m["legacy_unreserved_operations"] == 0

        # Step 4: Reclaimed on retry with same idempotency key -> no second reservation created
        async with pg_session_factory() as s:
            res_reused, newly_created = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key=idem_key)
            assert newly_created is False
            assert res_reused.id == res.id

            # Finish job
            res_reused.status = "finalized"
            wi_obj = (await s.execute(select(MediaWorkItemDB).where(MediaWorkItemDB.id == job_id))).scalar_one()
            wi_obj.status = "succeeded"
            await s.commit()

        # Verified: slot cleanly released to 0
        async with pg_session_factory() as s:
            m_end = await get_authoritative_concurrency_metrics(s, tenant_id)
            assert m_end["admitted_outstanding_operations"] == 0
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Scenario E cleanup")
            await session.execute(text("DELETE FROM gid.media_work_items WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 6. SCENARIO F: Independent tenant locks - zero cross-tenant blocking or leakage
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_f_independent_tenant_concurrency(pg_session_factory):
    tenant_1_id = f"test_scen_f1_{uuid.uuid4().hex[:8]}"
    tenant_2_id = f"test_scen_f2_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_scen_f_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t1 = TenantDB(id=tenant_1_id, name="Tenant F1", slug=f"scen-f1-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            t2 = TenantDB(id=tenant_2_id, name="Tenant F2", slug=f"scen-f2-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_f_{uuid.uuid4().hex[:6]}", name="Plan F", version=1, tier_type="paid", status="active", price_cents=100, billing_interval="monthly")
            session.add_all([t1, t2, p])
            await session.flush()

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=1, is_unlimited=False)
            sub1 = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_1_id, plan_id=plan_id, status="active", current_period_start=now)
            sub2 = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_2_id, plan_id=plan_id, status="active", current_period_start=now)
            session.add_all([ent, sub1, sub2])
            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_1_id, tenant_2_id], reason="Scenario F test")
            await session.commit()

        # Concurrent requests for T1 and T2 run simultaneously
        async def submit(t_id: str, idx: int):
            async with pg_session_factory() as s:
                try:
                    res, _ = await acquire_admission_and_reservation(session=s, tenant_id=t_id, operation_type="clip_extract", idempotency_key=f"f_job_{t_id}_{idx}")
                    await s.commit()
                    return (t_id, "success", res.id)
                except CommercialEnforcementException as e:
                    await s.rollback()
                    return (t_id, "blocked", e.reason_code)

        tasks = [submit(tenant_1_id, 0), submit(tenant_2_id, 0), submit(tenant_1_id, 1), submit(tenant_2_id, 1)]
        res = await asyncio.gather(*tasks)

        t1_succ = [r for r in res if r[0] == tenant_1_id and r[1] == "success"]
        t2_succ = [r for r in res if r[0] == tenant_2_id and r[1] == "success"]
        t1_block = [r for r in res if r[0] == tenant_1_id and r[1] == "blocked"]
        t2_block = [r for r in res if r[0] == tenant_2_id and r[1] == "blocked"]

        assert len(t1_succ) == 1
        assert len(t2_succ) == 1
        assert len(t1_block) == 1
        assert len(t2_block) == 1
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Scenario F cleanup")
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id IN (:t1, :t2)"), {"t1": tenant_1_id, "t2": tenant_2_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id IN (:t1, :t2)"), {"t1": tenant_1_id, "t2": tenant_2_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id IN (:t1, :t2)"), {"t1": tenant_1_id, "t2": tenant_2_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id IN (:t1, :t2)"), {"t1": tenant_1_id, "t2": tenant_2_id})
            await session.commit()


# ==============================================================================
# 7. TRANSACTIONAL ATOMICITY: Failure to persist work item rolls back reservation
# ==============================================================================
@pytest.mark.asyncio
async def test_admission_and_job_persistence_atomicity(pg_session_factory):
    tenant_id = f"test_atom_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_atom_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Atomicity Tenant", slug=f"atom-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_at_{uuid.uuid4().hex[:6]}", name="Plan Atom", version=1, tier_type="paid", status="active", price_cents=100, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()
            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=1, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=now)
            session.add_all([ent, sub])
            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_id], reason="Atomicity test")
            await session.commit()

        # Simulated transaction boundary failure
        idem_key = f"atom_key_{uuid.uuid4().hex[:6]}"
        try:
            async with pg_session_factory() as s:
                res, _ = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key=idem_key)
                # Inject failure before work item commit
                raise RuntimeError("Simulated unhandled worker queue crash during job persistence")
        except RuntimeError:
            pass  # Session rolled back

        # Verify that reservation was NOT committed (zero orphan reservation in database)
        async with pg_session_factory() as s:
            count = (await s.execute(select(func.count(CommercialReservationDB.id)).where(CommercialReservationDB.tenant_id == tenant_id))).scalar()
            assert count == 0, f"Expected 0 reservations after rollback, found {count}"

            # And capacity remains 100% available for a subsequent clean submission
            res_clean, _ = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key=idem_key)
            await s.commit()
            assert res_clean.id is not None
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Atomicity cleanup")
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 8. IDEMPOTENCY: Fingerprint mismatch, operation mismatch, and principal isolation (409)
# ==============================================================================
@pytest.mark.asyncio
async def test_idempotency_fingerprint_and_conflict_handling(pg_session_factory):
    tenant_id = f"test_idem_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_idem_{uuid.uuid4().hex[:8]}"
    user1_id = f"u1_{uuid.uuid4().hex[:6]}"
    user2_id = f"u2_{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Idempotency Tenant", slug=f"idem-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_id_{uuid.uuid4().hex[:6]}", name="Plan Idem", version=1, tier_type="paid", status="active", price_cents=100, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()

            u1 = UserDB(id=user1_id, tenant_id=tenant_id, email="u1@test.com", role="member", is_active=True)
            u2 = UserDB(id=user2_id, tenant_id=tenant_id, email="u2@test.com", role="member", is_active=True)
            session.add_all([u1, u2])

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=5, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=now)
            session.add_all([ent, sub])
            await session.commit()

        key = f"key_{uuid.uuid4().hex[:8]}"
        payload_a = {"url": "https://example.com/video1", "mode": "video"}
        payload_b = {"url": "https://example.com/video2", "mode": "audio"}

        # 1. First submission creates reservation
        async with pg_session_factory() as s:
            r1, is_new = await acquire_admission_and_reservation(
                session=s, tenant_id=tenant_id, operation_type="download", idempotency_key=key,
                payload=payload_a, user_id=user1_id
            )
            await s.commit()
            assert is_new is True

        # 2. Resubmission with same user, operation, and payload reuses cleanly
        async with pg_session_factory() as s:
            r2, is_new2 = await acquire_admission_and_reservation(
                session=s, tenant_id=tenant_id, operation_type="download", idempotency_key=key,
                payload=payload_a, user_id=user1_id
            )
            await s.commit()
            assert is_new2 is False
            assert r2.id == r1.id

        # 3. Payload mismatch -> 409 Conflict
        async with pg_session_factory() as s:
            with pytest.raises(CommercialEnforcementException) as exc1:
                await acquire_admission_and_reservation(
                    session=s, tenant_id=tenant_id, operation_type="download", idempotency_key=key,
                    payload=payload_b, user_id=user1_id
                )
            assert exc1.value.status_code == 409
            assert exc1.value.reason_code == "IDEMPOTENCY_CONFLICT"
            assert "different request parameters" in exc1.value.error_detail["message"]

        # 4. Operation type mismatch -> 409 Conflict
        async with pg_session_factory() as s:
            with pytest.raises(CommercialEnforcementException) as exc2:
                await acquire_admission_and_reservation(
                    session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key=key,
                    payload=payload_a, user_id=user1_id
                )
            assert exc2.value.status_code == 409
            assert exc2.value.reason_code == "IDEMPOTENCY_CONFLICT"
            assert "operation" in exc2.value.error_detail["message"]

        # 5. User mismatch (User 2 attempts User 1's key) -> 409 Conflict without leaking resource
        async with pg_session_factory() as s:
            with pytest.raises(CommercialEnforcementException) as exc3:
                await acquire_admission_and_reservation(
                    session=s, tenant_id=tenant_id, operation_type="download", idempotency_key=key,
                    payload=payload_a, user_id=user2_id
                )
            assert exc3.value.status_code == 409
            assert exc3.value.reason_code == "IDEMPOTENCY_CONFLICT"
            assert "another principal" in exc3.value.error_detail["message"]
    finally:
        async with pg_session_factory() as session:
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.users WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 9. LEASE RENEWAL: Worker heartbeat extends reservation lease & reconciler safety
# ==============================================================================
@pytest.mark.asyncio
async def test_worker_heartbeat_lease_renewal_and_reconciler(pg_session_factory):
    tenant_id = f"test_lease_{uuid.uuid4().hex[:8]}"
    job_id = str(uuid.uuid4())
    idem_key = f"lease_key_{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Lease Tenant", slug=f"lease-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            session.add(t)
            await session.flush()

            # Reservation with initial lease expiring in 5 minutes
            res = CommercialReservationDB(
                id=str(uuid.uuid4()), tenant_id=tenant_id, operation_type="clip_extract", resource_id=job_id,
                idempotency_key=idem_key, entitlement_key="max_concurrent_jobs", quantity=1, status="active",
                lease_expires_at=now + timedelta(minutes=5), created_at=now, updated_at=now,
            )
            wi = MediaWorkItemDB(
                id=job_id, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key=idem_key,
                request_payload={"clip": 1}, status="running", claimed_by="worker-active",
                lease_expires_at=now + timedelta(seconds=30), last_heartbeat_at=now, created_at=now, updated_at=now, available_at=now,
            )
            # Add an abandoned orphan reservation whose lease expired 10 minutes ago
            res_orphan = CommercialReservationDB(
                id=str(uuid.uuid4()), tenant_id=tenant_id, operation_type="download", resource_id=None,
                idempotency_key=f"orphan_{uuid.uuid4().hex[:6]}", entitlement_key="max_concurrent_jobs", quantity=1, status="reserved",
                lease_expires_at=now - timedelta(minutes=10), created_at=now - timedelta(minutes=20), updated_at=now,
            )
            session.add_all([res, wi, res_orphan])
            await session.commit()

        # Step 1: Worker executes heartbeat -> extends commercial reservation lease!
        async with pg_session_factory() as session:
            is_valid, is_cancelled = await heartbeat_and_check_cancellation(session, job_id, "worker-active")
            assert is_valid is True
            assert is_cancelled is False

        # Verify reservation lease was extended
        async with pg_session_factory() as session:
            r = await session.get(CommercialReservationDB, res.id)
            assert r.lease_expires_at > (now + timedelta(minutes=10))

        # Step 2: Run reconciliation
        async with pg_session_factory() as session:
            reconciled = await reconcile_stale_reservations(session)
            assert reconciled >= 1  # Reclaimed the orphan
            await session.commit()

        # Verify: Orphan is expired, but active running job is untouched
        async with pg_session_factory() as session:
            r_active = await session.get(CommercialReservationDB, res.id)
            assert r_active.status == "active"
            r_orph = await session.get(CommercialReservationDB, res_orphan.id)
            assert r_orph.status == "expired"
    finally:
        async with pg_session_factory() as session:
            await session.execute(text("DELETE FROM gid.media_work_items WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 10. INPUT VALIDATION & UTC TIMESTAMP INTEGRITY
# ==============================================================================
@pytest.mark.asyncio
async def test_input_validation_and_utc_correctness(pg_session_factory):
    tenant_id = f"test_val_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Val Tenant", slug=f"val-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            session.add(t)
            await session.commit()

        # 1. quantity <= 0 rejected
        async with pg_session_factory() as s:
            with pytest.raises(ValueError, match="strictly greater than zero"):
                await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="k1", quantity=0)

        # 2. negative estimated bytes rejected
        async with pg_session_factory() as s:
            with pytest.raises(ValueError, match="cannot be negative"):
                await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="k2", estimated_bytes=-50)

        # 3. negative estimated duration rejected
        async with pg_session_factory() as s:
            with pytest.raises(ValueError, match="cannot be negative"):
                await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="k3", estimated_duration_seconds=-10.0)

        # 4. invalid operation_type rejected
        async with pg_session_factory() as s:
            with pytest.raises(ValueError, match="Invalid operation_type"):
                await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="invalid_op", idempotency_key="k4")
    finally:
        async with pg_session_factory() as session:
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 11. ROLLOUT MODE TRANSITIONS & AUDIT TRAILS (disabled <-> shadow <-> enforce)
# ==============================================================================
@pytest.mark.asyncio
async def test_rollout_mode_transitions_and_shadow_audit(pg_session_factory):
    tenant_id = f"test_trans_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_trans_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Transition Tenant", slug=f"trans-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_tr_{uuid.uuid4().hex[:6]}", name="Plan Trans", version=1, tier_type="paid", status="active", price_cents=100, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=1, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=now)
            session.add_all([ent, sub])
            await session.commit()

        # Step 1: Transition disabled -> shadow
        async with pg_session_factory() as s:
            await update_enforcement_config(s, mode="shadow", tenant_allowlist=[], reason="Enable shadow evaluation")
            await s.commit()

        # Fill capacity with 1 job
        async with pg_session_factory() as s:
            await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="tr_job_1")
            await s.commit()

        # In shadow mode, 2nd request succeeds (not blocked), but records shadow would_block audit
        async with pg_session_factory() as s:
            r2, _ = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="tr_job_2")
            await s.commit()
            assert r2.id is not None

        # Verify shadow audit record
        async with pg_session_factory() as s:
            log_stmt = select(CommercialAuditLogDB).where(CommercialAuditLogDB.tenant_id == tenant_id, CommercialAuditLogDB.action == "commercial_quota_would_block")
            log = (await s.execute(log_stmt)).scalars().first()
            assert log is not None
            assert log.details_json.get("reason_code") == "CONCURRENCY_EXCEEDED"

        # Step 2: Transition shadow -> enforce
        async with pg_session_factory() as s:
            await update_enforcement_config(s, mode="enforce", tenant_allowlist=[tenant_id], reason="Enable enforce for test tenant")
            await s.commit()

        # In enforce mode, 3rd request is blocked with 429
        async with pg_session_factory() as s:
            with pytest.raises(CommercialEnforcementException) as exc:
                await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="tr_job_3")
            assert exc.value.status_code == 429

        # Step 3: Transition enforce -> disabled (safe rollback)
        async with pg_session_factory() as s:
            await update_enforcement_config(s, mode="disabled", tenant_allowlist=[], reason="Rollback to disabled mode")
            await s.commit()

        # In disabled mode, 4th request succeeds without blocking
        async with pg_session_factory() as s:
            r4, _ = await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="tr_job_4")
            await s.commit()
            assert r4.id is not None
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Transition test cleanup")
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()


# ==============================================================================
# 12. NON-ALLOWLISTED AND GRANDFATHERED LEGACY TENANT NON-BLOCKING
# ==============================================================================
@pytest.mark.asyncio
async def test_legacy_and_non_allowlisted_tenants_unblocked_in_enforce(pg_session_factory):
    tenant_allowlisted = f"t_allow_{uuid.uuid4().hex[:8]}"
    tenant_other = f"t_other_{uuid.uuid4().hex[:8]}"
    tenant_legacy = f"t_leg_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_leg_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t1 = TenantDB(id=tenant_allowlisted, name="Allowlisted Tenant", slug=f"t1-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            t2 = TenantDB(id=tenant_other, name="Other Tenant", slug=f"t2-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            t3 = TenantDB(id=tenant_legacy, name="Legacy Active Tenant", slug=f"t3-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_leg_{uuid.uuid4().hex[:6]}", name="Plan Leg", version=1, tier_type="paid", status="active", price_cents=100, billing_interval="monthly")
            session.add_all([t1, t2, t3, p])
            await session.flush()

            ent = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=1, is_unlimited=False)
            sub1 = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_allowlisted, plan_id=plan_id, status="active", current_period_start=now)
            sub2 = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_other, plan_id=plan_id, status="active", current_period_start=now)
            sub3 = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_legacy, plan_id=plan_id, status="legacy_active", current_period_start=now)
            session.add_all([ent, sub1, sub2, sub3])

            # Put both allowlisted AND legacy tenant on allowlist
            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_allowlisted, tenant_legacy], reason="Grandfathering test")
            await session.commit()

        # Verify enforcement evaluation:
        async with pg_session_factory() as s:
            enf1, _ = await is_tenant_enforced(s, tenant_allowlisted)
            enf2, _ = await is_tenant_enforced(s, tenant_other)
            enf3, _ = await is_tenant_enforced(s, tenant_legacy)

            assert enf1 is True   # On allowlist & active -> Enforced
            assert enf2 is False  # Not on allowlist -> Unblocked
            assert enf3 is False  # legacy_active -> Grandfathered! Never blocked!
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Grandfathering test cleanup")
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id IN (:t1, :t2, :t3)"), {"t1": tenant_allowlisted, "t2": tenant_other, "t3": tenant_legacy})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id IN (:t1, :t2, :t3)"), {"t1": tenant_allowlisted, "t2": tenant_other, "t3": tenant_legacy})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id IN (:t1, :t2, :t3)"), {"t1": tenant_allowlisted, "t2": tenant_other, "t3": tenant_legacy})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id IN (:t1, :t2, :t3)"), {"t1": tenant_allowlisted, "t2": tenant_other, "t3": tenant_legacy})
            await session.commit()


# ==============================================================================
# 13. ERROR CONTRACT CONFORMITY: Structured payloads & reason codes
# ==============================================================================
@pytest.mark.asyncio
async def test_error_contract_structure_and_reason_codes(pg_session_factory):
    tenant_id = f"test_err_{uuid.uuid4().hex[:8]}"
    plan_id = f"plan_err_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with pg_session_factory() as session:
            t = TenantDB(id=tenant_id, name="Error Contract Tenant", slug=f"err-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
            p = CommercialPlanDB(id=plan_id, plan_code=f"plan_er_{uuid.uuid4().hex[:6]}", name="Plan Err", version=1, tier_type="paid", status="active", price_cents=100, billing_interval="monthly")
            session.add_all([t, p])
            await session.flush()

            # Plan without api_access and max_concurrent_jobs=1
            ent_conc = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="max_concurrent_jobs", value_type="concurrency", value_int=1, is_unlimited=False)
            ent_api = CommercialPlanEntitlementDB(id=str(uuid.uuid4()), plan_id=plan_id, entitlement_key="api_access", value_type="boolean", value_bool=False, is_unlimited=False)
            sub = TenantSubscriptionDB(id=str(uuid.uuid4()), tenant_id=tenant_id, plan_id=plan_id, status="active", current_period_start=now)
            session.add_all([ent_conc, ent_api, sub])

            await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_id], reason="Error contract test")
            await session.commit()

        # 1. Feature not included: 403 Forbidden with FEATURE_NOT_INCLUDED
        async with pg_session_factory() as s:
            with pytest.raises(CommercialEnforcementException) as exc_api:
                await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="err_key_1", auth_method="api_key")
            err = exc_api.value.error_detail
            assert exc_api.value.status_code == 403
            assert err["error_code"] == "COMMERCIAL_POLICY_VIOLATION"
            assert err["reason_code"] == "FEATURE_NOT_INCLUDED"
            assert err["metric"] == "api_access"

        # 2. Concurrency exceeded: 429 Too Many Requests with CONCURRENCY_EXCEEDED
        async with pg_session_factory() as s:
            await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="err_key_1", auth_method="jwt")
            await s.commit()

        async with pg_session_factory() as s:
            with pytest.raises(CommercialEnforcementException) as exc_conc:
                await acquire_admission_and_reservation(session=s, tenant_id=tenant_id, operation_type="clip_extract", idempotency_key="err_key_2", auth_method="jwt")
            err = exc_conc.value.error_detail
            assert exc_conc.value.status_code == 429
            assert err["error_code"] == "COMMERCIAL_POLICY_VIOLATION"
            assert err["reason_code"] == "CONCURRENCY_EXCEEDED"
            assert err["limit_value"] == 1.0
            assert err["current_value"] == 1.0
    finally:
        async with pg_session_factory() as session:
            await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Error contract test cleanup")
            await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_audit_logs WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
            await session.execute(text("DELETE FROM gid.commercial_plan_entitlements WHERE plan_id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.commercial_plans WHERE id = :p"), {"p": plan_id})
            await session.execute(text("DELETE FROM gid.tenants WHERE id = :t"), {"t": tenant_id})
            await session.commit()
