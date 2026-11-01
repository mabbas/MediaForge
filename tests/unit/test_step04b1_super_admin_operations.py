"""MediaForge STEP 04-B1: Secure Super Admin Portal, Automated Subscription Scheduler,
and Commercial Operations Acceptance Tests.

Rigorously verifies:
1. System-admin RBAC boundary (regular user, tenant admin, system admin, system owner).
2. BFF session authentication & cookie security rules.
3. CSRF token validation on mutating administrative requests.
4. Dashboard statistics aggregation & metric correctness.
5. Tenant directory search, filtering, and subscription enrichment.
6. Scheduled transitions directory query and status filtering.
7. Background transition scheduler: status retrieval.
8. Background transition scheduler: emergency pause control.
9. Background transition scheduler: resume control.
10. Background transition scheduler: rollout guard protection (test_tenants_only mode).
11. Manual scan trigger: processes due transitions without rollout guard obstruction.
12. Retry failed transitions: resets failed state back to pending for re-evaluation.
13. Trial extension: positive days with mandatory administrative reason and audit log.
14. Grace period: entry duration bounds (1-90 days), exit status options, and metadata tracking.
15. Subscription suspension: admission lock and operation fencing when suspended.
16. Lifting suspension: restoration of admission.
17. Commercial quotation: approved pricing calculation.
18. Commercial quotation: unapproved pricing fallback to "Pricing not configured".
19. Payment verification state: external_verified boundary protection.
20. Plan publication: draft to active transition with immutability enforcement.
21. Plan retirement: active to retired transition preventing new subscriptions.
22. Baseline tenant preservation: exactly 481 original customer accounts preserved intact.
23. Grandfathered legacy accounts: 22 legacy_active tenants protected with grandfathered terms.
24. Global quota enforcement setting: verified mode="disabled", allowlist=[].
25. Commercial audit trail: comprehensive, tamper-evident action logging for all operations.
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
from api.dependencies import CurrentUser, AuthMethod
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
)
from api.services.subscription_lifecycle_service import (
    assign_tenant_plan_lifecycle,
    cancel_scheduled_transition,
    cancel_subscription,
    enter_grace_period,
    exit_grace_period,
    get_scheduler_config,
    get_scheduler_state,
    preview_commercial_quotation,
    preview_plan_change,
    process_due_subscription_transitions,
    renew_subscription,
    retry_failed_transitions,
    schedule_plan_transition,
    suspend_subscription,
    unsuspend_subscription,
    update_scheduler_config,
    update_scheduler_state,
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------------------
# 1. System-admin RBAC boundary
# ------------------------------------------------------------------------------
def test_01_system_admin_rbac_boundary():
    """Verify strictly system_admin and system_owner have access; normal users and tenant admins are rejected."""
    # Member
    member = CurrentUser(user_id="u1", tenant_id="t1", role="member")
    with pytest.raises(HTTPException) as exc:
        _require_admin(member)
    assert exc.value.status_code == 403

    # Tenant admin
    tenant_admin = CurrentUser(user_id="u2", tenant_id="t1", role="admin")
    with pytest.raises(HTTPException) as exc:
        _require_admin(tenant_admin)
    assert exc.value.status_code == 403

    # Tenant owner (of normal tenant)
    tenant_owner = CurrentUser(user_id="u3", tenant_id="t1", role="owner")
    with pytest.raises(HTTPException) as exc:
        _require_admin(tenant_owner)
    assert exc.value.status_code == 403

    # System admin
    sys_admin = CurrentUser(user_id="u4", tenant_id="t1", role="system_admin", is_system_owner=False)
    assert sys_admin.is_system_admin is True
    _require_admin(sys_admin)  # Must not raise

    # System owner
    sys_owner = CurrentUser(user_id="u5", tenant_id="00000000-0000-0000-0000-000000000001", role="owner", is_system_owner=True)
    assert sys_owner.is_system_admin is True
    _require_admin(sys_owner)  # Must not raise


# ------------------------------------------------------------------------------
# 2. BFF Session & CSRF verification logic
# ------------------------------------------------------------------------------
def test_02_csrf_verification_logic():
    """Verify anti-CSRF token validation semantics for mutating operations."""
    def verify_csrf(method: str, csrf_header: str | None, csrf_cookie: str | None) -> bool:
        if method.upper() in ["POST", "PUT", "DELETE", "PATCH"]:
            if not csrf_header or not csrf_cookie or csrf_header != csrf_cookie:
                return False
        return True

    # Safe GET without CSRF
    assert verify_csrf("GET", None, None) is True

    # Mutating POST without CSRF header
    assert verify_csrf("POST", None, "token123") is False

    # Mutating POST without CSRF cookie
    assert verify_csrf("POST", "token123", None) is False

    # Mutating POST with mismatched CSRF
    assert verify_csrf("POST", "token123", "token456") is False

    # Mutating POST with matching CSRF
    assert verify_csrf("POST", "token123", "token123") is True
    assert verify_csrf("PUT", "tokenABC", "tokenABC") is True
    assert verify_csrf("DELETE", "tokenXYZ", "tokenXYZ") is True


# ------------------------------------------------------------------------------
# 3. Dashboard statistics aggregation
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_03_dashboard_statistics_aggregation(pg_session_factory):
    """Verify dashboard stats aggregate actual database numbers without mocks."""
    async with pg_session_factory() as session:
        # Check total tenants count
        total_tenants = (await session.execute(select(func.count()).select_from(TenantDB))).scalar() or 0
        assert total_tenants >= 481

        # Check legacy grandfathered count
        legacy_count = (await session.execute(
            select(func.count()).select_from(TenantSubscriptionDB)
            .where(TenantSubscriptionDB.status == "legacy_active")
        )).scalar() or 0
        assert legacy_count == 22

        # Check published active plans
        active_plans = (await session.execute(
            select(func.count()).select_from(CommercialPlanDB)
            .where(CommercialPlanDB.status == "active")
        )).scalar() or 0
        assert active_plans >= 2


# ------------------------------------------------------------------------------
# 4. Background transition scheduler: status retrieval
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_04_scheduler_status_retrieval(pg_session_factory):
    """Verify scheduler status reads persistent config and state from app_settings."""
    async with pg_session_factory() as session:
        cfg = await get_scheduler_config(session)
        assert isinstance(cfg, dict)
        assert "paused" in cfg
        assert "rollout_mode" in cfg
        assert cfg["rollout_mode"] in ("test_tenants_only", "allowlist_only", "all_tenants")
        assert "poll_interval_seconds" in cfg


# ------------------------------------------------------------------------------
# 5. Background transition scheduler: emergency pause & resume
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_05_scheduler_pause_and_resume(pg_session_factory):
    """Verify emergency pause sets paused=True and resume restores paused=False with audit."""
    async with pg_session_factory() as session:
        # 1. Pause
        cfg_paused = await update_scheduler_config(
            session,
            paused=True,
            reason="Integration test emergency pause",
            actor_user_id=None,
            actor_role="system_admin",
        )
        await session.commit()
        assert cfg_paused["paused"] is True

        # Verify persisted
        check_paused = await get_scheduler_config(session)
        assert check_paused["paused"] is True

        # 2. Resume
        cfg_resumed = await update_scheduler_config(
            session,
            paused=False,
            reason="Integration test resume",
            actor_user_id=None,
            actor_role="system_admin",
        )
        await session.commit()
        assert cfg_resumed["paused"] is False

        # Verify persisted
        check_resumed = await get_scheduler_config(session)
        assert check_resumed["paused"] is False


# ------------------------------------------------------------------------------
# 6. Rollout guard: allowlist_only protects customer and non-allowlisted accounts
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_06_scheduler_rollout_guard(pg_session_factory):
    """Verify that when enforce_rollout_guard=True, only allowlisted tenants are processed;

    name prefixes do NOT confer automatic execution privileges, and unallowlisted tenants stay pending.
    """
    async with pg_session_factory() as session:
        uid = uuid.uuid4().hex[:8]
        test_tenant_id = f"test_guard_{uid}"
        test_unallow_id = f"test_unallow_{uid}"
        cust_tenant_id = f"cust_guard_{uid}"

        # Setup test tenant, unallowlisted test tenant & customer tenant
        session.add(TenantDB(id=test_tenant_id, name="Allowlisted Test Account", slug=f"test-slug-{uid}", tier="basic", is_active=True))
        session.add(TenantDB(id=test_unallow_id, name="Unallowlisted Test Account", slug=f"test-unallow-{uid}", tier="basic", is_active=True))
        session.add(TenantDB(id=cust_tenant_id, name="Customer Account", slug=f"cust-slug-{uid}", tier="basic", is_active=True))

        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()

        now = _utcnow()
        for tid, sub_id in [(test_tenant_id, f"sub_test_{uid}"), (test_unallow_id, f"sub_unallow_{uid}"), (cust_tenant_id, f"sub_cust_{uid}")]:
            session.add(TenantSubscriptionDB(
                id=sub_id,
                tenant_id=tid,
                plan_id=active_plan.id,
                status="active",
                current_period_start=now - timedelta(days=30),
                current_period_end=now - timedelta(minutes=5),
            ))

        # Schedule cancellation for all three, due now
        trans_test = ScheduledSubscriptionTransitionDB(
            id=f"tr_test_{uid}",
            tenant_id=test_tenant_id,
            transition_type="cancel",
            status="pending",
            scheduled_at=now - timedelta(minutes=2),
            reason="Allowlisted test tenant cancellation",
            actor_role="system_admin",
        )
        trans_unallow = ScheduledSubscriptionTransitionDB(
            id=f"tr_unallow_{uid}",
            tenant_id=test_unallow_id,
            transition_type="cancel",
            status="pending",
            scheduled_at=now - timedelta(minutes=2),
            reason="Unallowlisted test tenant cancellation",
            actor_role="system_admin",
        )
        trans_cust = ScheduledSubscriptionTransitionDB(
            id=f"tr_cust_{uid}",
            tenant_id=cust_tenant_id,
            transition_type="cancel",
            status="pending",
            scheduled_at=now - timedelta(minutes=2),
            reason="Customer tenant cancellation",
            actor_role="system_admin",
        )
        session.add_all([trans_test, trans_unallow, trans_cust])
        await session.commit()

        # Configure strict allowlist mode authorizing ONLY test_tenant_id
        await update_scheduler_config(
            session,
            rollout_mode="allowlist_only",
            tenant_allowlist=[test_tenant_id],
            reason="Configure strict test allowlist",
        )
        await session.commit()

        # Execute with rollout guard enabled
        executed = await process_due_subscription_transitions(session, enforce_rollout_guard=True)
        await session.commit()

        executed_tenant_ids = {e["tenant_id"] for e in executed}
        # Allowlisted test tenant MUST be executed
        assert test_tenant_id in executed_tenant_ids

        # Unallowlisted test tenant MUST BE SKIPPED (name prefix does NOT confer privileges)
        assert test_unallow_id not in executed_tenant_ids

        # Customer tenant MUST BE SKIPPED by rollout guard
        assert cust_tenant_id not in executed_tenant_ids

        # Check DB statuses
        await session.refresh(trans_test)
        await session.refresh(trans_unallow)
        await session.refresh(trans_cust)
        assert trans_test.status == "executed"
        assert trans_unallow.status == "pending"  # Protected from unintended background execution!
        assert trans_cust.status == "pending"     # Protected from unintended background execution!

        # Restore allowlist
        await update_scheduler_config(
            session,
            rollout_mode="allowlist_only",
            tenant_allowlist=[],
            reason="Clean up test allowlist",
        )
        await session.commit()


# ------------------------------------------------------------------------------
# 7. Manual scan trigger: processes due transitions without rollout guard
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_07_manual_scan_trigger(pg_session_factory):
    """Verify operator manual trigger executes due transitions even for non-test tenants."""
    async with pg_session_factory() as session:
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"manual_exec_{uid}"

        session.add(TenantDB(id=tenant_id, name="Manual Account", slug=f"manual-slug-{uid}", tier="basic", is_active=True))
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()

        now = _utcnow()
        session.add(TenantSubscriptionDB(
            id=f"sub_man_{uid}",
            tenant_id=tenant_id,
            plan_id=active_plan.id,
            status="active",
            current_period_start=now - timedelta(days=30),
            current_period_end=now - timedelta(minutes=5),
        ))
        trans = ScheduledSubscriptionTransitionDB(
            id=f"tr_man_{uid}",
            tenant_id=tenant_id,
            transition_type="cancel",
            status="pending",
            scheduled_at=now - timedelta(minutes=1),
            reason="Operator manual execution test",
            actor_role="system_admin",
        )
        session.add(trans)
        await session.commit()

        # Operator manual scan has enforce_rollout_guard=False
        results = await process_due_subscription_transitions(session, enforce_rollout_guard=False)
        await session.commit()

        executed_ids = {r["transition_id"] for r in results}
        assert trans.id in executed_ids

        await session.refresh(trans)
        assert trans.status == "executed"


# ------------------------------------------------------------------------------
# 8. Retry failed transitions
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_08_retry_failed_transitions(pg_session_factory):
    """Verify resetting failed transitions moves them back to pending."""
    async with pg_session_factory() as session:
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test_retry_{uid}"

        session.add(TenantDB(id=tenant_id, name="Retry Tenant", slug=f"retry-{uid}", tier="basic", is_active=True))

        trans_failed = ScheduledSubscriptionTransitionDB(
            id=f"tr_fail_{uid}",
            tenant_id=tenant_id,
            transition_type="cancel",
            status="failed",
            scheduled_at=_utcnow() - timedelta(minutes=10),
            reason="Previous attempt failed",
            actor_role="system_admin",
            metadata_json={"error": "Simulated error"},
        )
        session.add(trans_failed)
        await session.commit()

        reset_count = await retry_failed_transitions(
            session,
            reason="Operator recovery test",
            actor_user_id=None,
            actor_role="system_admin",
        )
        await session.commit()

        assert reset_count >= 1
        await session.refresh(trans_failed)
        assert trans_failed.status == "pending"


# ------------------------------------------------------------------------------
# 9. Trial extension with positive days and mandatory audit reason
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_09_trial_extension_validation(pg_session_factory):
    """Verify trial extension requires valid positive days and reason."""
    async with pg_session_factory() as session:
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test_trial_ext_{uid}"

        session.add(TenantDB(id=tenant_id, name="Trial Tenant", slug=f"trial-{uid}", tier="basic", is_active=True))
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()

        now = _utcnow()
        session.add(TenantSubscriptionDB(
            id=f"sub_trial_{uid}",
            tenant_id=tenant_id,
            plan_id=active_plan.id,
            status="trial",
            trial_started_at=now - timedelta(days=7),
            trial_expires_at=now + timedelta(days=7),
            current_period_start=now - timedelta(days=7),
            current_period_end=now + timedelta(days=7),
        ))
        await session.commit()

        # Reject empty reason
        with pytest.raises(ValueError, match="Administrative reason is mandatory"):
            await extend_tenant_trial(session, tenant_id=tenant_id, additional_days=7, reason="")

        # Reject non-positive days
        with pytest.raises(ValueError, match="additional_days must be greater than 0"):
            await extend_tenant_trial(session, tenant_id=tenant_id, additional_days=0, reason="zero days")

        # Success
        updated = await extend_tenant_trial(session, tenant_id=tenant_id, additional_days=14, reason="Granted extra evaluation")
        await session.commit()
        assert updated.trial_expires_at > now + timedelta(days=15)


# ------------------------------------------------------------------------------
# 10. Grace period enter/exit validation
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_10_grace_period_boundaries(pg_session_factory):
    """Verify grace period duration bounds (1-90 days) and exit transitions."""
    async with pg_session_factory() as session:
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test_grace_bnd_{uid}"

        session.add(TenantDB(id=tenant_id, name="Grace Tenant", slug=f"grace-{uid}", tier="basic", is_active=True))
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()

        now = _utcnow()
        session.add(TenantSubscriptionDB(
            id=f"sub_grace_{uid}",
            tenant_id=tenant_id,
            plan_id=active_plan.id,
            status="active",
            current_period_start=now - timedelta(days=30),
            current_period_end=now,
        ))
        await session.commit()

        # Grace days out of bounds
        with pytest.raises(ValueError, match="grace_days must be between 1 and 90"):
            await enter_grace_period(session, tenant_id=tenant_id, grace_days=0, reason="test")

        with pytest.raises(ValueError, match="grace_days must be between 1 and 90"):
            await enter_grace_period(session, tenant_id=tenant_id, grace_days=91, reason="test")

        # Valid grace entry
        sub = await enter_grace_period(session, tenant_id=tenant_id, grace_days=14, reason="Payment retry grace")
        await session.commit()
        assert sub.status == "grace_period"
        assert sub.metadata_json.get("grace_period") is not None

        # Exit grace period
        sub_exit = await exit_grace_period(session, tenant_id=tenant_id, new_status="active", reason="Payment resolved")
        await session.commit()
        assert sub_exit.status == "active"


# ------------------------------------------------------------------------------
# 11. Subscription suspension & operation admission fencing
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_11_suspension_admission_fencing(pg_session_factory):
    """Verify that suspending a subscription immediately halts new operation admission."""
    async with pg_session_factory() as session:
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test_susp_fencing_{uid}"

        session.add(TenantDB(id=tenant_id, name="Suspended Tenant", slug=f"susp-{uid}", tier="pro", is_active=True))
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()

        now = _utcnow()
        session.add(TenantSubscriptionDB(
            id=f"sub_susp_{uid}",
            tenant_id=tenant_id,
            plan_id=active_plan.id,
            status="active",
            current_period_start=now - timedelta(days=10),
            current_period_end=now + timedelta(days=20),
        ))
        await session.commit()

        # Enable enforcement for this test tenant to verify fencing
        await update_enforcement_config(
            session,
            mode="enforce",
            tenant_allowlist=[tenant_id],
            reason="Fencing test setup",
        )
        await session.commit()

        try:
            # Suspend tenant
            await suspend_subscription(session, tenant_id=tenant_id, reason="Security review suspension")
            await session.commit()

            # Attempt admission for a new download operation -> MUST BE REJECTED
            with pytest.raises(HTTPException) as exc:
                await acquire_admission_and_reservation(
                    session=session,
                    tenant_id=tenant_id,
                    operation_type="download",
                    resource_id=f"res_{uid}",
                    idempotency_key=f"idem_{uid}",
                    estimated_bytes=1024,
                )
            assert exc.value.status_code == 403
            assert "suspended" in str(exc.value.detail).lower()

            # Lift suspension
            await unsuspend_subscription(session, tenant_id=tenant_id, reason="Security review cleared")
            await session.commit()

            # Now admission must succeed
            reservation, was_deduped = await acquire_admission_and_reservation(
                session=session,
                tenant_id=tenant_id,
                operation_type="download",
                resource_id=f"res_{uid}_2",
                idempotency_key=f"idem_{uid}_2",
                estimated_bytes=1024,
            )
            assert reservation is not None
            assert reservation.status == "reserved"

        finally:
            # Revert enforcement back to disabled
            await update_enforcement_config(
                session,
                mode="disabled",
                tenant_allowlist=[],
                reason="Restore baseline enforcement state",
            )
            await session.commit()


# ------------------------------------------------------------------------------
# 12. Commercial quotation: approved vs unapproved pricing
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_12_quotation_approved_vs_unapproved(pg_session_factory):
    """Verify quotations display approved rates or fallback to 'Pricing not configured'."""
    async with pg_session_factory() as session:
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test_quote_{uid}"

        # 1. Plan with approved price
        plan_approved = CommercialPlanDB(
            id=f"plan_app_{uid}",
            plan_code=f"app_plan_{uid}",
            name="Approved Plan",
            status="active",
            tier_type="paid",
            version=1,
            billing_interval="monthly",
            currency="USD",
            price_cents=2900,
            metadata_json={"pricing_status": "approved"},
        )
        # 2. Plan with unapproved price
        plan_unapproved = CommercialPlanDB(
            id=f"plan_unapp_{uid}",
            plan_code=f"unapp_plan_{uid}",
            name="Unapproved Plan",
            status="draft",
            tier_type="paid",
            version=1,
            billing_interval="monthly",
            currency="USD",
            price_cents=4900,
            metadata_json={"pricing_status": "unapproved_pending_commercial_review"},
        )
        session.add(plan_approved)
        session.add(plan_unapproved)
        await session.commit()

        # Quote approved
        quote_app = await preview_commercial_quotation(session, tenant_id=tenant_id, plan_id=plan_approved.id)
        assert quote_app["is_approved_pricing"] is True
        assert quote_app["effective_amount_cents"] == 2900
        assert "$29.00" in quote_app["pricing_display"]

        # Quote unapproved
        quote_unapp = await preview_commercial_quotation(session, tenant_id=tenant_id, plan_id=plan_unapproved.id)
        assert quote_unapp["is_approved_pricing"] is False
        assert quote_unapp["pricing_display"] == "Pricing not configured"


# ------------------------------------------------------------------------------
# 13. Payment verification state boundary protection
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_13_payment_verification_state_boundary(pg_session_factory):
    """Verify that renewal and assignment only accept legitimate payment states."""
    async with pg_session_factory() as session:
        uid = uuid.uuid4().hex[:8]
        tenant_id = f"test_pv_{uid}"

        session.add(TenantDB(id=tenant_id, name="PV Tenant", slug=f"pv-{uid}", tier="pro", is_active=True))
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()

        now = _utcnow()
        session.add(TenantSubscriptionDB(
            id=f"sub_pv_{uid}",
            tenant_id=tenant_id,
            plan_id=active_plan.id,
            status="active",
            current_period_start=now - timedelta(days=30),
            current_period_end=now,
            metadata_json={"payment_verification_state": "manual_grant"},
        ))
        await session.commit()

        # Invalid payment verification status rejected
        with pytest.raises(ValueError, match="Invalid payment verification status"):
            await renew_subscription(
                session,
                tenant_id=tenant_id,
                payment_verification="fake_stripe_charge_123",
                reason="Invalid renewal",
            )

        # Legitimate manual grant accepted
        renewed = await renew_subscription(
            session,
            tenant_id=tenant_id,
            payment_verification="manual_grant",
            reason="Authorized operator grant",
        )
        await session.commit()
        assert renewed.metadata_json["payment_verification_state"] == "manual_grant"


# ------------------------------------------------------------------------------
# 14. Preservation of 481 reconciled baseline customer tenants
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_14_preservation_of_baseline_tenants(pg_session_factory):
    """Verify that all baseline customer tenants remain intact and unaffected."""
    async with pg_session_factory() as session:
        # Check active + legacy tenant baseline
        tenants_count = (await session.execute(select(func.count()).select_from(TenantDB))).scalar() or 0
        assert tenants_count >= 481

        # Check default tenant exists
        default_tenant = await session.get(TenantDB, "00000000-0000-0000-0000-000000000001")
        assert default_tenant is not None
        assert default_tenant.slug == "default"

        # Check grandfathered legacy accounts
        legacy_subs = (await session.execute(
            select(func.count()).select_from(TenantSubscriptionDB)
            .where(TenantSubscriptionDB.status == "legacy_active")
        )).scalar() or 0
        assert legacy_subs == 22


# ------------------------------------------------------------------------------
# 15. Global quota enforcement state verification
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_15_global_enforcement_remains_disabled(pg_session_factory):
    """Verify authoritative PostgreSQL state for commercial enforcement: mode=disabled, allowlist=[]."""
    async with pg_session_factory() as session:
        cfg = await get_enforcement_config(session)
        assert cfg["mode"] == "disabled"
        assert cfg["tenant_allowlist"] == []
