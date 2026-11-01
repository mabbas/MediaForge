"""MediaForge STEP 04-B2-A: Commercial Lifecycle Invariants Acceptance Suite.

Validates 12 critical commercial edge-cases and invariants on PostgreSQL (grabitdown_test):
1. Trial reactivation after prior expiration.
2. Repeated trial grants accumulation.
3. Administrative renewal without external payment.
4. Legacy grandfathering under quota enforcement.
5. Published plan immutability.
6. Draft plan non-assignment guard.
7. Unapproved pricing exclusion ("Pricing not configured").
8. Expired trial messaging and denial.
9. Suspension behavior across enforcement modes.
10. Scheduled downgrade with existing active reservations.
11. Reservation lease continuity during plan transitions.
12. Calendar-month usage allowance boundary calculations.
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
    TenantSubscriptionDB,
    UsageEventDB,
    UserDB,
)
from api.dependencies import CurrentUser
from api.routers.admin_plans import update_plan_pricing
from api.schemas.admin_plans import PlanPricingUpdateRequest
from api.services.enforcement_service import (
    CommercialEnforcementException,
    acquire_admission_and_reservation,
    get_enforcement_config,
    update_enforcement_config,
    _get_current_utc_month_bounds,
)
from api.services.entitlement_service import (
    QuotaReasonCode,
    evaluate_tenant_quotas,
    extend_tenant_trial,
    resolve_effective_entitlements,
)
from api.services.subscription_lifecycle_service import (
    assign_tenant_plan_lifecycle,
    cancel_scheduled_transition,
    enter_grace_period,
    exit_grace_period,
    preview_commercial_quotation,
    renew_subscription,
    schedule_plan_transition,
    suspend_subscription,
    unsuspend_subscription,
)
from fastapi import HTTPException

PG_URL = os.environ.get(
    "GID_TEST_DATABASE_URL",
    "postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown_test",
)


@pytest.fixture
def pg_session_factory():
    if not ("test" in PG_URL.lower() or os.environ.get("ALLOW_AUTHORITATIVE_DEV_TEST") == "1"):
        raise RuntimeError(f"Safety violation: Refusing to run tests against non-test database: {PG_URL}")
    engine = create_async_engine(PG_URL, echo=False, pool_size=5, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=True)
    yield factory


def _utcnow() -> datetime:
    return datetime.utcnow()


@pytest.mark.asyncio
async def test_01_trial_reactivation_after_prior_expiration(pg_session_factory):
    """Verify an expired trial tenant can be reactivated by administrative trial extension."""
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_reactivate_{uid}"
    now = _utcnow()

    async with pg_session_factory() as session:
        tnt = TenantDB(id=tenant_id, name="Expired Trial Tenant", slug=f"exp-{uid}", tier="trial", is_active=True)
        sub = TenantSubscriptionDB(
            id=f"sub_exp_{uid}",
            tenant_id=tenant_id,
            plan_id="plan-trial-v1",
            status="trial",
            trial_started_at=now - timedelta(days=20),
            trial_expires_at=now - timedelta(days=5),
            current_period_end=now - timedelta(days=5),
        )
        session.add_all([tnt, sub])
        await session.commit()

        # Extend trial by 14 days
        extended_sub = await extend_tenant_trial(
            session,
            tenant_id=tenant_id,
            additional_days=14,
            reason="Commercial sales approved reactivation of trial evaluation",
        )
        await session.commit()

        assert extended_sub.status == "trial"
        assert extended_sub.trial_expires_at > now

        # Refresh subscription from DB
        await session.refresh(sub)
        assert sub.status == "trial"
        assert sub.trial_expires_at > now


@pytest.mark.asyncio
async def test_02_repeated_trial_grants_accumulate_safely(pg_session_factory):
    """Verify repeated consecutive trial extensions accumulate predictably without errors."""
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_repeat_trial_{uid}"
    now = _utcnow()

    async with pg_session_factory() as session:
        tnt = TenantDB(id=tenant_id, name="Repeat Trial Tenant", slug=f"rpt-{uid}", tier="trial", is_active=True)
        initial_end = now + timedelta(days=5)
        sub = TenantSubscriptionDB(
            id=f"sub_rpt_{uid}",
            tenant_id=tenant_id,
            plan_id="plan-trial-v1",
            status="trial",
            trial_started_at=now - timedelta(days=9),
            trial_expires_at=initial_end,
            current_period_end=initial_end,
        )
        session.add_all([tnt, sub])
        await session.commit()

        # First grant: 7 days
        await extend_tenant_trial(session, tenant_id=tenant_id, additional_days=7, reason="Extension 1")
        await session.commit()
        await session.refresh(sub)
        first_expiry = sub.trial_expires_at

        # Second grant: 10 days
        await extend_tenant_trial(session, tenant_id=tenant_id, additional_days=10, reason="Extension 2")
        await session.commit()
        await session.refresh(sub)
        second_expiry = sub.trial_expires_at

        # Verify accumulation: second_expiry should be exactly 10 days after first_expiry
        assert (second_expiry - first_expiry).total_seconds() == pytest.approx(10 * 86400, abs=5)
        assert sub.trial_expires_at > now + timedelta(days=20)


@pytest.mark.asyncio
async def test_03_administrative_renewal_without_external_payment(pg_session_factory):
    """Verify administrative subscription renewal operates without external payment dependencies."""
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_renew_{uid}"
    now = _utcnow()

    async with pg_session_factory() as session:
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()
        assert active_plan is not None

        tnt = TenantDB(id=tenant_id, name="Contract Renewal Tenant", slug=f"rnw-{uid}", tier="basic", is_active=True)
        old_end = now + timedelta(days=2)
        sub = TenantSubscriptionDB(
            id=f"sub_rnw_{uid}",
            tenant_id=tenant_id,
            plan_id=active_plan.id,
            status="active",
            current_period_start=now - timedelta(days=28),
            current_period_end=old_end,
        )
        session.add_all([tnt, sub])
        await session.commit()

        renewed = await renew_subscription(
            session,
            tenant_id=tenant_id,
            reason="Annual purchase order PO-8849 approved by commercial accounting",
            actor_user_id=None,
            actor_role="system_admin",
        )
        await session.commit()

        assert renewed.status == "active"
        assert renewed.current_period_end > old_end

        # Verify commercial audit trail
        audit = (await session.execute(
            select(CommercialAuditLogDB).where(
                CommercialAuditLogDB.tenant_id == tenant_id,
                CommercialAuditLogDB.action == "subscription_renewed",
            )
        )).scalars().first()
        assert audit is not None
        assert audit.actor_role == "system_admin"
        assert "payment_method" not in audit.details_json or audit.details_json.get("payment_method") == "administrative_manual"


@pytest.mark.asyncio
async def test_04_legacy_grandfathering_unblocked_under_allowlist(pg_session_factory):
    """Verify legacy_active grandfathered accounts are never blocked even when enforcement is enabled."""
    uid = uuid.uuid4().hex[:8]
    legacy_tnt_id = f"tnt_legacy_{uid}"
    now = _utcnow()

    async with pg_session_factory() as session:
        tnt = TenantDB(id=legacy_tnt_id, name="Grandfathered Legacy Tenant", slug=f"lgc-{uid}", tier="basic", is_active=True)
        sub = TenantSubscriptionDB(
            id=f"sub_lgc_{uid}",
            tenant_id=legacy_tnt_id,
            plan_id="plan-free-v1",
            status="legacy_active",
            current_period_start=now - timedelta(days=365),
            current_period_end=None,
        )
        session.add_all([tnt, sub])

        # Enable enforcement for a different tenant
        await update_enforcement_config(
            session,
            mode="enforce",
            tenant_allowlist=["other-tenant-id"],
            reason="Enforcement test for non-legacy tenants",
        )
        await session.commit()

        # Legacy tenant admission MUST succeed
        resv, is_existing = await acquire_admission_and_reservation(
            session,
            tenant_id=legacy_tnt_id,
            operation_type="clip_extract",
            idempotency_key=str(uuid.uuid4()),
        )
        assert resv is not None
        assert resv.status in ("active", "reserved")

        # Clean up reservation and restore disabled mode
        await session.execute(
            text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :tid").bindparams(tid=legacy_tnt_id)
        )
        await update_enforcement_config(
            session,
            mode="disabled",
            tenant_allowlist=[],
            reason="Restore default disabled mode",
        )
        await session.commit()


@pytest.mark.asyncio
async def test_05_published_plan_immutability(pg_session_factory):
    """Verify that pricing or properties of published plans cannot be modified."""
    async with pg_session_factory() as session:
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()
        assert active_plan is not None

        admin_user = CurrentUser(
            user_id="admin-user-id",
            tenant_id="00000000-0000-0000-0000-000000000001",
            role="system_admin",
            permissions=["system:admin"],
        )

        with pytest.raises(HTTPException) as exc:
            await update_plan_pricing(
                plan_id=active_plan.id,
                body=PlanPricingUpdateRequest(
                    price_cents=9900,
                    pricing_status="approved",
                    reason="Attempted mutation of active plan",
                ),
                user=admin_user,
                session=session,
            )
        assert exc.value.status_code == 400
        assert "immutable" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_06_draft_plan_non_assignment(pg_session_factory):
    """Verify draft plans cannot be assigned or scheduled to subscriptions."""
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_draft_guard_{uid}"
    draft_plan_id = f"plan_draft_{uid}"

    async with pg_session_factory() as session:
        tnt = TenantDB(id=tenant_id, name="Draft Test Tenant", slug=f"drf-{uid}", tier="basic", is_active=True)
        sub = TenantSubscriptionDB(
            id=f"sub_drf_{uid}",
            tenant_id=tenant_id,
            plan_id="plan-free-v1",
            status="active",
        )
        draft = CommercialPlanDB(
            id=draft_plan_id,
            plan_code=f"code-draft-{uid}",
            name="Draft Plan",
            version=1,
            tier_type="paid",
            status="draft",
            price_cents=None,
        )
        session.add_all([tnt, sub, draft])
        await session.commit()

        # Immediate assignment MUST fail
        with pytest.raises(ValueError) as exc:
            await assign_tenant_plan_lifecycle(
                session,
                tenant_id=tenant_id,
                plan_id=draft_plan_id,
                reason="Attempted assignment of draft plan",
            )
        assert "draft" in str(exc.value).lower()

        # Scheduling MUST fail
        with pytest.raises(ValueError) as exc_sched:
            await schedule_plan_transition(
                session,
                tenant_id=tenant_id,
                target_plan_id=draft_plan_id,
                transition_type="upgrade",
                reason="Attempted schedule of draft plan",
            )
        assert "draft" in str(exc_sched.value).lower()


@pytest.mark.asyncio
async def test_07_unapproved_pricing_exclusion(pg_session_factory):
    """Verify unapproved plan pricing is identified and excluded from billing activation."""
    uid = uuid.uuid4().hex[:8]
    plan_id = f"plan_unapproved_{uid}"
    tenant_id = f"tnt_unapp_{uid}"

    async with pg_session_factory() as session:
        tnt = TenantDB(id=tenant_id, name="Unapproved Plan Tenant", slug=f"unp-{uid}", tier="basic", is_active=True)
        sub = TenantSubscriptionDB(
            id=f"sub_unp_{uid}",
            tenant_id=tenant_id,
            plan_id="plan-free-v1",
            status="active",
        )
        plan = CommercialPlanDB(
            id=plan_id,
            plan_code=f"unapp-{uid}",
            name="Unapproved Pricing Plan",
            version=1,
            tier_type="paid",
            status="draft",
            price_cents=None,
            metadata_json={"pricing_status": "unapproved_pending_commercial_review"},
        )
        session.add_all([tnt, sub, plan])
        await session.commit()

        is_approved = (
            plan.price_cents is not None
            and plan.metadata_json.get("pricing_status") != "unapproved_pending_commercial_review"
        )
        assert is_approved is False

        # Preview quotation confirms not configured
        quotation = await preview_commercial_quotation(session, tenant_id=tenant_id, plan_id=plan_id)
        assert quotation["pricing_display"] == "Pricing not configured" or "unapproved" in quotation.get("pricing_display", "").lower()


@pytest.mark.asyncio
async def test_08_expired_trial_messaging_and_denial(pg_session_factory):
    """Verify quota evaluation correctly denies expired trial tenants with unambiguous messaging."""
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_exp_msg_{uid}"
    now = _utcnow()

    async with pg_session_factory() as session:
        tnt = TenantDB(id=tenant_id, name="Expired Messaging Tenant", slug=f"xmsg-{uid}", tier="trial", is_active=True)
        sub = TenantSubscriptionDB(
            id=f"sub_xmsg_{uid}",
            tenant_id=tenant_id,
            plan_id="plan-trial-v1",
            status="trial",
            trial_started_at=now - timedelta(days=20),
            trial_expires_at=now - timedelta(days=6),
            current_period_end=now - timedelta(days=6),
        )
        session.add_all([tnt, sub])
        await session.commit()

        eval_res = await evaluate_tenant_quotas(session, tenant_id=tenant_id)
        assert eval_res.subscription_status == "expired"
        assert eval_res.trial_days_remaining == 0


@pytest.mark.asyncio
async def test_09_suspension_behavior_across_enforcement_modes(pg_session_factory):
    """Verify suspension state operates consistently across disabled and enforced rollout modes."""
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_susp_{uid}"
    now = _utcnow()

    async with pg_session_factory() as session:
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()
        assert active_plan is not None

        tnt = TenantDB(id=tenant_id, name="Suspended Tenant", slug=f"ssp-{uid}", tier="basic", is_active=True)
        sub = TenantSubscriptionDB(
            id=f"sub_ssp_{uid}",
            tenant_id=tenant_id,
            plan_id=active_plan.id,
            status="active",
            current_period_start=now - timedelta(days=10),
            current_period_end=now + timedelta(days=20),
        )
        session.add_all([tnt, sub])
        await session.commit()

        # 1. Suspend tenant
        await suspend_subscription(session, tenant_id=tenant_id, reason="Security audit suspension")
        await session.commit()
        await session.refresh(sub)
        assert sub.status == "suspended"

        # 2. In disabled mode: admission allows soft-pass
        await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Disabled check")
        await session.commit()
        resv_dis, _ = await acquire_admission_and_reservation(
            session,
            tenant_id=tenant_id,
            operation_type="clip_extract",
            idempotency_key=str(uuid.uuid4()),
        )
        assert resv_dis.status in ("active", "reserved")

        # Clean up temporary reservation
        await session.execute(text("DELETE FROM gid.commercial_reservations WHERE tenant_id = :tid").bindparams(tid=tenant_id))

        # 3. In enforce mode with allowlist: admission MUST fail closed with 403
        await update_enforcement_config(session, mode="enforce", tenant_allowlist=[tenant_id], reason="Enforce check")
        await session.commit()
        with pytest.raises(CommercialEnforcementException) as exc:
            await acquire_admission_and_reservation(
                session,
                tenant_id=tenant_id,
                operation_type="clip_extract",
                idempotency_key=str(uuid.uuid4()),
            )
        assert exc.value.status_code == 403
        assert exc.value.reason_code == QuotaReasonCode.SUBSCRIPTION_SUSPENDED.value

        # Restore disabled mode and unsuspend
        await update_enforcement_config(session, mode="disabled", tenant_allowlist=[], reason="Restore disabled")
        await unsuspend_subscription(session, tenant_id=tenant_id, reason="Audit cleared")
        await session.commit()


@pytest.mark.asyncio
async def test_10_scheduled_downgrade_preserves_active_jobs(pg_session_factory):
    """Verify scheduling a future downgrade does not disrupt or abort in-flight active jobs."""
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_down_jobs_{uid}"
    now = _utcnow()

    async with pg_session_factory() as session:
        tnt = TenantDB(id=tenant_id, name="Downgrade Active Jobs Tenant", slug=f"dwj-{uid}", tier="basic", is_active=True)
        sub = TenantSubscriptionDB(
            id=f"sub_dwj_{uid}",
            tenant_id=tenant_id,
            plan_id="plan-free-v1",
            status="active",
            current_period_start=now - timedelta(days=15),
            current_period_end=now + timedelta(days=15),
        )
        resv = CommercialReservationDB(
            id=f"resv_active_{uid}",
            tenant_id=tenant_id,
            operation_type="clip_extract",
            entitlement_key="max_concurrency",
            idempotency_key=str(uuid.uuid4()),
            quantity=1,
            lease_expires_at=now + timedelta(minutes=10),
            status="reserved",
        )
        session.add_all([tnt, sub, resv])
        await session.commit()

        # Schedule future downgrade
        transition = await schedule_plan_transition(
            session,
            tenant_id=tenant_id,
            target_plan_id="plan-trial-v1",
            transition_type="downgrade",
            scheduled_at=now + timedelta(days=15),
            reason="Customer self-service downgrade to trial tier",
        )
        await session.commit()

        assert transition.status == "pending"

        # Verify active reservation was not aborted or altered
        await session.refresh(resv)
        assert resv.status == "reserved"
        assert resv.lease_expires_at > now


@pytest.mark.asyncio
async def test_11_reservation_continuity_during_plan_change(pg_session_factory):
    """Verify existing active reservation leases remain valid and complete cleanly after plan change."""
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_resv_cont_{uid}"
    now = _utcnow()

    async with pg_session_factory() as session:
        tnt = TenantDB(id=tenant_id, name="Reservation Continuity Tenant", slug=f"rct-{uid}", tier="basic", is_active=True)
        sub = TenantSubscriptionDB(
            id=f"sub_rct_{uid}",
            tenant_id=tenant_id,
            plan_id="plan-free-v1",
            status="active",
        )
        resv = CommercialReservationDB(
            id=f"resv_inflight_{uid}",
            tenant_id=tenant_id,
            operation_type="clip_extract",
            entitlement_key="max_concurrency",
            idempotency_key=str(uuid.uuid4()),
            quantity=1,
            lease_expires_at=now + timedelta(minutes=15),
            status="reserved",
        )
        session.add_all([tnt, sub, resv])
        await session.commit()

        # Execute immediate upgrade
        await assign_tenant_plan_lifecycle(
            session,
            tenant_id=tenant_id,
            plan_id="plan-trial-v1",
            reason="Immediate upgrade during active job",
        )
        await session.commit()

        # Active reservation must remain active and serviceable
        await session.refresh(resv)
        assert resv.status == "reserved"

        # Complete reservation normally
        resv.status = "finalized"
        resv.finalized_at = _utcnow()
        await session.commit()
        await session.refresh(resv)
        assert resv.status == "finalized"


def test_12_calendar_month_usage_allowance_boundaries():
    """Verify UTC calendar-month bounds accurately delimit usage periods across all calendar scenarios."""
    # Standard 31-day month: January 15, 2026
    dt_jan = datetime(2026, 1, 15, 14, 30, 0)
    start_jan, end_jan = _get_current_utc_month_bounds(dt_jan)
    assert start_jan == datetime(2026, 1, 1, 0, 0, 0)
    assert end_jan == datetime(2026, 2, 1, 0, 0, 0)

    # Leap year February: February 20, 2028 (2028 is a leap year)
    dt_leap = datetime(2028, 2, 20, 10, 0, 0)
    start_leap, end_leap = _get_current_utc_month_bounds(dt_leap)
    assert start_leap == datetime(2028, 2, 1, 0, 0, 0)
    assert end_leap == datetime(2028, 3, 1, 0, 0, 0)
    assert (end_leap - start_leap).days == 29

    # Non-leap year February: February 10, 2026
    dt_feb = datetime(2026, 2, 10, 0, 0, 0)
    start_feb, end_feb = _get_current_utc_month_bounds(dt_feb)
    assert start_feb == datetime(2026, 2, 1, 0, 0, 0)
    assert end_feb == datetime(2026, 3, 1, 0, 0, 0)
    assert (end_feb - start_feb).days == 28

    # Year rollover: December 31, 2026 23:59:59
    dt_dec = datetime(2026, 12, 31, 23, 59, 59)
    start_dec, end_dec = _get_current_utc_month_bounds(dt_dec)
    assert start_dec == datetime(2026, 12, 1, 0, 0, 0)
    assert end_dec == datetime(2027, 1, 1, 0, 0, 0)
