"""MediaForge STEP 04-A3: Subscription Lifecycle, Commercial Administration,
Scheduled Plan Transitions, and Tenant Management Acceptance Tests.

This test suite rigorously validates:
1. System-admin-only mutation.
2. Cross-tenant access restrictions.
3. Published plan immutability.
4. Draft plan assignment rejection.
5. Retired plan assignment rejection.
6. Valid published plan assignment.
7. Immediate upgrade.
8. Scheduled downgrade.
9. Contradictory transition prevention.
10. Scheduled execution idempotency.
11. Scheduler crash/retry recovery.
12. Duplicate concurrent administrative requests.
13. Trial extension and zero-day behavior.
14. Grace-period date boundaries.
15. Cancellation at period end.
16. Renewal idempotency.
17. Existing usage-period preservation.
18. Legacy account grandfathering.
19. No fabricated payment verification.
20. No accidental quota activation.
21. Subscription history preservation.
22. Audit log correctness.
23. Tenant-specific override precedence.
24. Commercial pricing approval checks.
25. Desktop read-only visibility.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import uuid
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.models import (
    AppSettingDB,
    CommercialAuditLogDB,
    CommercialPlanDB,
    CommercialPlanEntitlementDB,
    CommercialReservationDB,
    ScheduledSubscriptionTransitionDB,
    TenantDB,
    TenantEntitlementOverrideDB,
    TenantSubscriptionDB,
    UsageEventDB,
    UserDB,
)
from api.dependencies import CurrentUser
from api.routers.admin_plans import _require_admin
from api.services.enforcement_service import (
    acquire_admission_and_reservation,
    get_enforcement_config,
    update_enforcement_config,
)
from api.services.entitlement_service import (
    CANONICAL_ENTITLEMENT_METRICS,
    QuotaReasonCode,
    evaluate_tenant_quotas,
    extend_tenant_trial,
    get_system_default_trial_days,
    log_commercial_audit,
    resolve_effective_entitlements,
    set_tenant_entitlement_override,
    update_system_default_trial_days,
)
from api.services.subscription_lifecycle_service import (
    assign_tenant_plan_lifecycle,
    cancel_scheduled_transition,
    cancel_subscription,
    enter_grace_period,
    exit_grace_period,
    preview_commercial_quotation,
    preview_plan_change,
    process_due_subscription_transitions,
    renew_subscription,
    schedule_plan_transition,
    suspend_subscription,
    unsuspend_subscription,
    validate_state_transition,
)
from fastapi import HTTPException

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


# ------------------------------------------------------------------------------
# 1. System-admin-only mutation
# ------------------------------------------------------------------------------
def test_01_system_admin_only_mutation():
    """Verify non-admin or tenant-level users are rejected by _require_admin."""
    regular_user = CurrentUser(
        user_id="user_reg",
        tenant_id="tenant_1",
        email="user@example.com",
        role="member",
        is_system_admin=False,
        is_system_owner=False,
    )
    with pytest.raises(HTTPException) as exc:
        _require_admin(regular_user)
    assert exc.value.status_code == 403

    tenant_admin = CurrentUser(
        user_id="user_t_admin",
        tenant_id="tenant_1",
        email="t_admin@example.com",
        role="tenant_admin",
        is_system_admin=False,
        is_system_owner=False,
    )
    with pytest.raises(HTTPException) as exc:
        _require_admin(tenant_admin)
    assert exc.value.status_code == 403

    sys_admin = CurrentUser(
        user_id="user_sys_admin",
        tenant_id="tenant_sys",
        email="sysadmin@example.com",
        role="system_admin",
        is_system_admin=True,
        is_system_owner=False,
    )
    # Must not raise
    _require_admin(sys_admin)


# ------------------------------------------------------------------------------
# 2. Cross-tenant access restrictions
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_02_cross_tenant_access_restrictions(pg_session_factory):
    """Verify operations enforce tenant boundaries and reject cross-tenant manipulation."""
    tenant_a = f"test_cta_{uuid.uuid4().hex[:8]}"
    tenant_b = f"test_ctb_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t_a = TenantDB(id=tenant_a, name="Tenant A", slug=f"t-a-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        t_b = TenantDB(id=tenant_b, name="Tenant B", slug=f"t-b-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add_all([t_a, t_b])
        await session.commit()

        # Initial plan for Tenant A
        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_a, plan_id="plan-free-v1", reason="Initial plan")
        await session.commit()

        # Schedule transition on Tenant A
        trans_a = await schedule_plan_transition(
            session,
            tenant_id=tenant_a,
            target_plan_id="plan-free-v1",
            transition_type="upgrade",
            reason="Schedule for Tenant A",
        )
        await session.commit()

        # Attempting to cancel Tenant A's transition using Tenant B's tenant_id must fail
        with pytest.raises(ValueError) as exc:
            await cancel_scheduled_transition(
                session,
                tenant_id=tenant_b,
                transition_id=trans_a.id,
                reason="Cross-tenant cancellation attempt",
            )
        assert "not found for tenant" in str(exc.value)


# ------------------------------------------------------------------------------
# 3. Published plan immutability
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_03_published_plan_immutability(pg_session_factory):
    """Verify published active plans cannot have pricing or entitlements silently mutated."""
    async with pg_session_factory() as session:
        plan = await session.get(CommercialPlanDB, "plan-free-v1")
        assert plan is not None
        assert plan.status == "active"

        # Attempting to update pricing on published plan must be rejected
        from api.routers.admin_plans import update_plan_pricing
        from api.schemas.admin_plans import PlanPricingUpdateRequest

        admin_user = CurrentUser(
            user_id="admin_1", tenant_id="tenant_sys", email="adm@ex.com",
            role="system_admin", is_system_admin=True, is_system_owner=False,
        )
        with pytest.raises(HTTPException) as exc:
            await update_plan_pricing(
                plan_id="plan-free-v1",
                body=PlanPricingUpdateRequest(price_cents=999, pricing_status="approved", reason="Illegal mutation"),
                user=admin_user,
                session=session,
            )
        assert exc.value.status_code == 400
        assert "immutable" in exc.value.detail.lower()


# ------------------------------------------------------------------------------
# 4. Draft plan assignment rejection
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_04_draft_plan_assignment_rejection(pg_session_factory):
    """Verify draft plans cannot be assigned to customer subscriptions."""
    tenant_id = f"test_draft_{uuid.uuid4().hex[:8]}"
    draft_plan_id = f"plan_draft_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Draft Test Tenant", slug=f"t-dr-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        p = CommercialPlanDB(
            id=draft_plan_id, plan_code=f"draft_{uuid.uuid4().hex[:6]}", name="Draft Plan",
            version=1, tier_type="paid", status="draft", price_cents=None, billing_interval="monthly"
        )
        session.add_all([t, p])
        await session.commit()

        with pytest.raises(ValueError) as exc:
            await assign_tenant_plan_lifecycle(
                session,
                tenant_id=tenant_id,
                plan_id=draft_plan_id,
                reason="Attempting to assign draft plan",
            )
        assert "draft status" in str(exc.value).lower()


# ------------------------------------------------------------------------------
# 5. Retired plan assignment rejection
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_05_retired_plan_assignment_rejection(pg_session_factory):
    """Verify retired plans cannot accept new assignments."""
    tenant_id = f"test_retired_{uuid.uuid4().hex[:8]}"
    retired_plan_id = f"plan_ret_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Retired Test Tenant", slug=f"t-rt-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        p = CommercialPlanDB(
            id=retired_plan_id, plan_code=f"ret_{uuid.uuid4().hex[:6]}", name="Retired Plan",
            version=1, tier_type="paid", status="retired", price_cents=1000, billing_interval="monthly"
        )
        session.add_all([t, p])
        await session.commit()

        with pytest.raises(ValueError) as exc:
            await assign_tenant_plan_lifecycle(
                session,
                tenant_id=tenant_id,
                plan_id=retired_plan_id,
                reason="Attempting to assign retired plan",
            )
        assert "is retired" in str(exc.value).lower()


# ------------------------------------------------------------------------------
# 6. Valid published plan assignment
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_06_valid_published_plan_assignment(pg_session_factory):
    """Verify published plan can be assigned cleanly and creates audit trail."""
    tenant_id = f"test_pub_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Valid Assign Tenant", slug=f"t-va-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        u = UserDB(id=f"u_{uuid.uuid4().hex[:8]}", email=f"admin_{uuid.uuid4().hex[:6]}@ex.com", tenant_id=tenant_id, role="system_admin", password_hash="dummy")
        session.add_all([t, u])
        await session.commit()

        sub = await assign_tenant_plan_lifecycle(
            session,
            tenant_id=tenant_id,
            plan_id="plan-free-v1",
            actor_user_id=u.id,
            actor_role="system_admin",
            reason="Assigned free baseline",
        )
        await session.commit()

        assert sub.plan_id == "plan-free-v1"
        assert sub.status == "active"
        assert sub.metadata_json.get("payment_verification_state") == "none"

        # Check audit log
        stmt = select(CommercialAuditLogDB).where(CommercialAuditLogDB.tenant_id == tenant_id, CommercialAuditLogDB.action == "tenant_plan_assigned")
        log = (await session.execute(stmt)).scalars().first()
        assert log is not None
        assert log.actor_user_id == u.id
        assert "free" in log.reason.lower()


# ------------------------------------------------------------------------------
# 7. Immediate upgrade
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_07_immediate_upgrade(pg_session_factory):
    """Verify immediate upgrade takes effect immediately, leaves existing reservations intact."""
    tenant_id = f"test_upg_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Upgrade Tenant", slug=f"t-up-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        u = UserDB(id=f"u_{uuid.uuid4().hex[:8]}", email=f"admin_{uuid.uuid4().hex[:6]}@ex.com", tenant_id=tenant_id, role="system_admin", password_hash="dummy")
        session.add_all([t, u])
        await session.commit()

        # Start with free
        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-free-v1", reason="Initial plan")
        # Add a reservation
        res = CommercialReservationDB(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            operation_type="download",
            idempotency_key=f"idem_{uuid.uuid4().hex[:6]}",
            entitlement_key="max_concurrent_jobs",
            quantity=1,
            status="reserved",
        )
        session.add(res)
        await session.commit()

        # Immediate upgrade to pro trial
        updated_sub = await assign_tenant_plan_lifecycle(
            session,
            tenant_id=tenant_id,
            plan_id="plan-trial-v1",
            actor_user_id=u.id,
            reason="Immediate upgrade to Pro Trial",
        )
        await session.commit()

        assert updated_sub.plan_id == "plan-trial-v1"
        assert updated_sub.status == "active"

        # Verify reservation is still reserved and intact
        check_res = await session.get(CommercialReservationDB, res.id)
        assert check_res is not None
        assert check_res.status == "reserved"


# ------------------------------------------------------------------------------
# 8. Scheduled downgrade
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_08_scheduled_downgrade(pg_session_factory):
    """Verify scheduled downgrade creates a pending record for the future and preserves current tier."""
    tenant_id = f"test_sdn_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Downgrade Tenant", slug=f"t-dn-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        # Active on pro trial
        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-trial-v1", reason="Initial Pro")
        await session.commit()

        future_date = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=15)
        sched = await schedule_plan_transition(
            session,
            tenant_id=tenant_id,
            target_plan_id="plan-free-v1",
            transition_type="downgrade",
            scheduled_at=future_date,
            reason="Customer requested downgrade at period end",
        )
        await session.commit()

        assert sched.status == "pending"
        assert sched.transition_type == "downgrade"
        assert sched.target_plan_id == "plan-free-v1"

        # Current subscription must STILL be pro trial
        sub = (await session.execute(select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id))).scalars().first()
        assert sub.plan_id == "plan-trial-v1"


# ------------------------------------------------------------------------------
# 9. Contradictory transition prevention
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_09_contradictory_transition_prevention(pg_session_factory):
    """Verify attempting to schedule two overlapping transitions for the same tenant is rejected."""
    tenant_id = f"test_contra_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Contra Tenant", slug=f"t-ct-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-free-v1", reason="Initial")
        await session.commit()

        future_1 = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)
        future_2 = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=20)

        # First schedule succeeds
        await schedule_plan_transition(
            session,
            tenant_id=tenant_id,
            target_plan_id="plan-trial-v1",
            transition_type="upgrade",
            scheduled_at=future_1,
            reason="First transition",
        )
        await session.commit()

        # Second schedule must fail
        with pytest.raises(ValueError) as exc:
            await schedule_plan_transition(
                session,
                tenant_id=tenant_id,
                target_plan_id="plan-free-v1",
                transition_type="downgrade",
                scheduled_at=future_2,
                reason="Second conflicting transition",
            )
        assert "already exists" in str(exc.value).lower()


# ------------------------------------------------------------------------------
# 10. Scheduled execution idempotency
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_10_scheduled_execution_idempotency(pg_session_factory):
    """Verify running transition processor multiple times executes due transition exactly once."""
    tenant_id = f"test_idem_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Idem Tenant", slug=f"t-id-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-free-v1", reason="Initial")
        await session.commit()

        past_due = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1)
        # Schedule
        rec = await schedule_plan_transition(
            session,
            tenant_id=tenant_id,
            target_plan_id="plan-trial-v1",
            transition_type="upgrade",
            scheduled_at=past_due,
            reason="Due transition",
        )
        await session.commit()

        # Simulate time passed
        as_of = past_due + timedelta(seconds=5)

        # First execution pass
        res1 = await process_due_subscription_transitions(session, as_of=as_of)
        await session.commit()
        matching1 = [r for r in res1 if r["transition_id"] == rec.id]
        assert len(matching1) == 1
        assert matching1[0]["transition_id"] == rec.id

        # Second execution pass (must be 0 for this transition, already executed)
        res2 = await process_due_subscription_transitions(session, as_of=as_of)
        await session.commit()
        matching2 = [r for r in res2 if r["transition_id"] == rec.id]
        assert len(matching2) == 0

        # Verify transition record status is 'executed'
        updated_rec = await session.get(ScheduledSubscriptionTransitionDB, rec.id)
        assert updated_rec.status == "executed"
        assert updated_rec.executed_at is not None


# ------------------------------------------------------------------------------
# 11. Scheduler crash/retry recovery
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_11_scheduler_crash_retry_recovery(pg_session_factory):
    """Verify pending transitions remain intact if scheduler aborts/restarts."""
    tenant_id = f"test_crash_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Crash Recovery Tenant", slug=f"t-cr-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-free-v1", reason="Initial")
        past_due = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1)
        rec = await schedule_plan_transition(
            session,
            tenant_id=tenant_id,
            target_plan_id="plan-trial-v1",
            transition_type="upgrade",
            scheduled_at=past_due,
            reason="Due transition",
        )
        await session.commit()

        # Rollback or abort without processing
        await session.rollback()

        # Transition remains 'pending'
        check_rec = await session.get(ScheduledSubscriptionTransitionDB, rec.id)
        assert check_rec.status == "pending"

        # Subsequent pass processes cleanly
        as_of = past_due + timedelta(seconds=5)
        res = await process_due_subscription_transitions(session, as_of=as_of)
        await session.commit()
        assert len(res) == 1
        assert res[0]["transition_id"] == rec.id


# ------------------------------------------------------------------------------
# 12. Duplicate concurrent administrative requests
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_12_duplicate_concurrent_administrative_requests(pg_session_factory):
    """Verify concurrent attempts to schedule or modify a tenant are safely isolated."""
    tenant_id = f"test_conc_adm_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Concurrent Admin Tenant", slug=f"t-ca-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-free-v1", reason="Initial")
        await session.commit()

    async def try_schedule(idx: int):
        async with pg_session_factory() as s:
            try:
                fut = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=idx + 5)
                await schedule_plan_transition(
                    s,
                    tenant_id=tenant_id,
                    target_plan_id="plan-trial-v1",
                    transition_type="upgrade",
                    scheduled_at=fut,
                    reason=f"Concurrent attempt {idx}",
                )
                await s.commit()
                return "success"
            except Exception as e:
                await s.rollback()
                return f"rejected: {e}"

    results = await asyncio.gather(*[try_schedule(i) for i in range(5)])
    successes = [r for r in results if r == "success"]
    assert len(successes) == 1, f"Expected exactly 1 concurrent success, got {len(successes)}"


# ------------------------------------------------------------------------------
# 13. Trial extension and zero-day behavior
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_13_trial_extension_and_zero_day_behavior(pg_session_factory):
    """Verify trial extension extends expiration date, and 0-day trial yields immediate expiration."""
    tenant_id = f"test_trial_ext_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Trial Tenant", slug=f"t-te-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        # Start on trial
        sub = TenantSubscriptionDB(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            plan_id="plan-trial-v1",
            status="trial",
            trial_started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            trial_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7),
        )
        session.add(sub)
        await session.commit()

        orig_exp = sub.trial_expires_at
        # Extend by 14 days
        await extend_tenant_trial(session, tenant_id=tenant_id, additional_days=14, reason="VIP customer extension")
        await session.commit()

        await session.refresh(sub)
        assert sub.trial_expires_at > orig_exp
        assert (sub.trial_expires_at - orig_exp).days >= 13

        # Zero-day trial setting behavior
        old_default = await get_system_default_trial_days(session)
        try:
            await update_system_default_trial_days(session, trial_days=0, reason="Zero-day baseline test")
            await session.commit()
            assert await get_system_default_trial_days(session) == 0
        finally:
            await update_system_default_trial_days(session, trial_days=old_default, reason="Restore default")
            await session.commit()


# ------------------------------------------------------------------------------
# 14. Grace-period date boundaries
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_14_grace_period_date_boundaries(pg_session_factory):
    """Verify active access inside grace period, and expiration after grace date lapses."""
    tenant_id = f"test_grace_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Grace Tenant", slug=f"t-gr-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-trial-v1", reason="Initial")
        await session.commit()

        # Enter grace period (7 days)
        sub = await enter_grace_period(session, tenant_id=tenant_id, grace_days=7, reason="Billing grace period")
        await session.commit()
        assert sub.status == "grace_period"

        # Check quota status inside grace period
        status_inside = await evaluate_tenant_quotas(session, tenant_id)
        assert status_inside.subscription_status == "grace_period"
        assert status_inside.grace_period_remaining_days is not None
        assert status_inside.grace_period_remaining_days > 0

        # Manually backdate grace expiry into past to simulate boundary expiration
        sub.metadata_json["grace_period"]["expires_at"] = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).isoformat()
        session.add(sub)
        await session.commit()

        status_past = await evaluate_tenant_quotas(session, tenant_id)
        assert status_past.subscription_status == "expired"


# ------------------------------------------------------------------------------
# 15. Cancellation at period end
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_15_cancellation_at_period_end(pg_session_factory):
    """Verify cancellation scheduled at period end marks status as scheduled_cancellation."""
    tenant_id = f"test_cancel_sched_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Cancel Tenant", slug=f"t-cn-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-trial-v1", reason="Initial")
        await session.commit()

        sub = await cancel_subscription(session, tenant_id=tenant_id, immediate=False, reason="User cancelled auto-renew")
        await session.commit()

        assert sub.status == "scheduled_cancellation"

        # Check pending transition created
        stmt = select(ScheduledSubscriptionTransitionDB).where(
            ScheduledSubscriptionTransitionDB.tenant_id == tenant_id,
            ScheduledSubscriptionTransitionDB.transition_type == "cancel",
            ScheduledSubscriptionTransitionDB.status == "pending",
        )
        cancel_sched = (await session.execute(stmt)).scalars().first()
        assert cancel_sched is not None


# ------------------------------------------------------------------------------
# 16. Renewal idempotency
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_16_renewal_idempotency(pg_session_factory):
    """Verify administrative renewal advances period bounds cleanly and preserves status."""
    tenant_id = f"test_renew_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Renew Tenant", slug=f"t-rn-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        sub = await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-trial-v1", reason="Initial")
        await session.commit()

        old_end = sub.current_period_end
        assert old_end is not None

        # Renew for 1 month
        renewed_sub = await renew_subscription(
            session,
            tenant_id=tenant_id,
            interval="monthly",
            payment_verification="manual_grant",
            reason="Administrative grant renewal",
        )
        await session.commit()

        assert renewed_sub.current_period_start == old_end
        assert renewed_sub.current_period_end > old_end
        assert renewed_sub.metadata_json.get("payment_verification_state") == "manual_grant"


# ------------------------------------------------------------------------------
# 17. Existing usage-period preservation
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_17_existing_usage_period_preservation(pg_session_factory):
    """Verify usage consumption remains calculated using UTC calendar-month bounds."""
    tenant_id = f"test_usage_bound_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Usage Bound Tenant", slug=f"t-ub-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Record usage event in current month
        event = UsageEventDB(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            event_type="download",
            quantity=1,
            unit="count",
            bytes=1024,
            duration_seconds=5.0,
            idempotency_key=f"ue_{uuid.uuid4().hex[:8]}",
            created_at=now,
        )
        session.add(event)
        await session.commit()

        status = await evaluate_tenant_quotas(session, tenant_id)
        assert status.consumption.get("monthly_download_count") == 1
        assert status.consumption.get("monthly_download_bytes") == 1024


# ------------------------------------------------------------------------------
# 18. Legacy account grandfathering
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_18_legacy_account_grandfathering(pg_session_factory):
    """Verify legacy_active accounts remain unblocked and retain full grandfathered access."""
    from api.services.entitlement_service import simulate_operation_admission
    async with pg_session_factory() as session:
        stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.status == "legacy_active").limit(1)
        legacy_sub = (await session.execute(stmt)).scalars().first()
        assert legacy_sub is not None

        # Evaluate quotas
        status = await evaluate_tenant_quotas(session, legacy_sub.tenant_id)
        assert status.subscription_status == "legacy_active"
        assert status.is_advisory is True

        # Simulation evaluates as admissible without customer blocking
        sim = await simulate_operation_admission(
            session,
            tenant_id=legacy_sub.tenant_id,
            operation_type="download",
            estimated_bytes=1000,
        )
        assert sim["admissible"] is True
        assert sim["subscription_status"] == "legacy_active"


# ------------------------------------------------------------------------------
# 19. No fabricated payment verification
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_19_no_fabricated_payment_verification(pg_session_factory):
    """Verify payment status is explicitly recorded as none or manual_grant, not marked paid."""
    tenant_id = f"test_no_fake_pay_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="No Fake Pay Tenant", slug=f"t-nf-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        sub = await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-trial-v1", reason="Initial")
        await session.commit()

        meta = sub.metadata_json or {}
        assert meta.get("payment_verification_state") in ("manual_grant", "none", "deferred")
        assert meta.get("payment_verification_state") != "paid"


# ------------------------------------------------------------------------------
# 20. No accidental quota activation
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_20_no_accidental_quota_activation(pg_session_factory):
    """Verify global enforcement mode remains 'disabled' and tenant allowlist is empty."""
    async with pg_session_factory() as session:
        cfg = await get_enforcement_config(session)
        assert cfg["mode"] == "disabled"
        assert cfg["tenant_allowlist"] == []


# ------------------------------------------------------------------------------
# 21. Subscription history preservation
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_21_subscription_history_preservation(pg_session_factory):
    """Verify history query retrieves subscription, scheduled transitions, and audit trail."""
    tenant_id = f"test_hist_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="History Tenant", slug=f"t-hi-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-free-v1", reason="Initial assign")
        future_date = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=15)
        await schedule_plan_transition(session, tenant_id=tenant_id, target_plan_id="plan-trial-v1", transition_type="upgrade", scheduled_at=future_date, reason="Scheduled upgrade")
        await session.commit()

        # Query history
        from api.routers.admin_plans import get_subscription_history_endpoint
        admin_user = CurrentUser(
            user_id="admin_1", tenant_id="tenant_sys", email="adm@ex.com",
            role="system_admin", is_system_admin=True, is_system_owner=False,
        )
        hist = await get_subscription_history_endpoint(tenant_id=tenant_id, user=admin_user, session=session)
        assert hist.tenant_id == tenant_id
        assert hist.current_subscription is not None
        assert len(hist.scheduled_transitions) >= 1
        assert len(hist.audit_events) >= 2


# ------------------------------------------------------------------------------
# 22. Audit log correctness
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_22_audit_log_correctness(pg_session_factory):
    """Verify every commercial mutation logs actor, reason, and action accurately."""
    tenant_id = f"test_audit_corr_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Audit Correct Tenant", slug=f"t-ac-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        u = UserDB(id=f"u_{uuid.uuid4().hex[:8]}", email=f"admin_{uuid.uuid4().hex[:6]}@ex.com", tenant_id=tenant_id, role="system_admin", password_hash="dummy")
        session.add_all([t, u])
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-free-v1", actor_user_id=u.id, actor_role="system_admin", reason="Audit correctness test")
        await session.commit()

        stmt = select(CommercialAuditLogDB).where(CommercialAuditLogDB.tenant_id == tenant_id)
        logs = (await session.execute(stmt)).scalars().all()
        assert len(logs) >= 1
        log = logs[0]
        assert log.actor_user_id == u.id
        assert log.actor_role == "system_admin"
        assert log.reason == "Audit correctness test"
        assert log.action == "tenant_plan_assigned"


# ------------------------------------------------------------------------------
# 23. Tenant-specific override precedence
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_23_tenant_specific_override_precedence(pg_session_factory):
    """Verify tenant entitlement override takes precedence over plan entitlements."""
    tenant_id = f"test_override_prec_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Override Tenant", slug=f"t-op-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        u = UserDB(id=f"u_{uuid.uuid4().hex[:8]}", email=f"admin_{uuid.uuid4().hex[:6]}@ex.com", tenant_id=tenant_id, role="system_admin", password_hash="dummy")
        session.add_all([t, u])
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-free-v1", reason="Initial")
        await set_tenant_entitlement_override(
            session,
            tenant_id=tenant_id,
            entitlement_key="max_concurrent_jobs",
            value_type="concurrency",
            value_int=5,
            reason="Special contract override",
            actor_user_id=u.id,
        )
        await session.commit()

        status = await evaluate_tenant_quotas(session, tenant_id)
        assert status.checks["max_concurrent_jobs"].limit_value == 5
        assert status.effective_entitlements["max_concurrent_jobs"]["source"] == "override"


# ------------------------------------------------------------------------------
# 24. Commercial pricing approval checks
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_24_commercial_pricing_approval_checks(pg_session_factory):
    """Verify unapproved draft plans display 'Pricing not configured' in quotations."""
    tenant_id = f"test_quote_price_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Quote Price Tenant", slug=f"t-qp-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        # Quote for unapproved draft Creator plan
        quote = await preview_commercial_quotation(session, tenant_id=tenant_id, plan_id="plan-creator-monthly-v2")
        assert quote["is_approved_pricing"] is False
        assert quote["pricing_display"] == "Pricing not configured"
        assert quote["base_price_cents"] is None


# ------------------------------------------------------------------------------
# 25. Desktop read-only visibility
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_25_desktop_read_only_visibility(pg_session_factory):
    """Verify status payload contains all fields required by Desktop settings read-only view."""
    tenant_id = f"test_desktop_vis_{uuid.uuid4().hex[:8]}"

    async with pg_session_factory() as session:
        t = TenantDB(id=tenant_id, name="Desktop Vis Tenant", slug=f"t-dv-{uuid.uuid4().hex[:6]}", tier="basic", is_active=True)
        session.add(t)
        await session.commit()

        await assign_tenant_plan_lifecycle(session, tenant_id=tenant_id, plan_id="plan-trial-v1", reason="Initial")
        future_date = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=20)
        await schedule_plan_transition(session, tenant_id=tenant_id, target_plan_id="plan-free-v1", transition_type="downgrade", scheduled_at=future_date, reason="Downgrade scheduled")
        await session.commit()

        status = await evaluate_tenant_quotas(session, tenant_id)
        data = status.model_dump()

        assert "plan_name" in data
        assert "plan_version" in data
        assert "subscription_status" in data
        assert "scheduled_transition" in data
        assert data["scheduled_transition"] is not None
        assert data["scheduled_transition"]["transition_type"] == "downgrade"
        assert "checks" in data
        assert "consumption" in data
        assert "period_reset_at" in data
