"""Administrative commercial plan catalog, tenant subscription, and trial configuration endpoints (STEP 04-A1)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.database.models import (
    CommercialAuditLogDB,
    CommercialPlanDB,
    CommercialPlanEntitlementDB,
    CommercialReservationDB,
    ScheduledSubscriptionTransitionDB,
    TenantDB,
    TenantEntitlementOverrideDB,
    TenantSubscriptionDB,
)
from api.dependencies import CurrentUser, get_current_user
from api.schemas.admin_plans import (
    AssignPlanRequest,
    AuditLogResponse,
    CancelSubscriptionRequest,
    DashboardStatsResponse,
    EnforcementSettingResponse,
    EntitlementOverrideRequest,
    ExitGracePeriodRequest,
    ExtendTrialRequest,
    GracePeriodRequest,
    PauseSchedulerRequest,
    PlanChangePreviewResponse,
    PlanCreateRequest,
    PlanPricingUpdateRequest,
    PlanResponse,
    QuotationPreviewRequest,
    QuotationPreviewResponse,
    RenewSubscriptionRequest,
    ReservationResponse,
    ResumeSchedulerRequest,
    RetryFailedTransitionsRequest,
    RevokeOverrideRequest,
    ScheduleTransitionRequest,
    ScheduledTransitionListResponse,
    ScheduledTransitionResponse,
    SchedulerStatusResponse,
    SubscriptionHistoryResponse,
    SuspendSubscriptionRequest,
    TenantListItemResponse,
    TenantListResponse,
    TrialSettingResponse,
    TriggerSchedulerResponse,
    UnsuspendSubscriptionRequest,
    UpdateDefaultTrialRequest,
    UpdateEnforcementSettingRequest,
)
from api.services.entitlement_service import (
    CANONICAL_ENTITLEMENT_METRICS,
    assign_plan_to_tenant,
    evaluate_tenant_quotas,
    extend_tenant_trial,
    log_commercial_audit,
    revoke_tenant_entitlement_override,
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
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Commercial Admin"])


def _require_admin(user: CurrentUser) -> None:
    """Enforce strict system-level administrative authentication boundary."""
    if not (user.is_system_admin or user.is_system_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Commercial plan administration requires system administrator privileges",
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _format_plan_response(plan: CommercialPlanDB, entitlements: list[CommercialPlanEntitlementDB]) -> PlanResponse:
    ent_map = {}
    for e in entitlements:
        ent_map[e.entitlement_key] = {
            "value_type": e.value_type,
            "value_bool": e.value_bool,
            "value_int": e.value_int,
            "is_unlimited": e.is_unlimited,
        }
    return PlanResponse(
        id=plan.id,
        plan_code=plan.plan_code,
        name=plan.name,
        description=plan.description,
        status=plan.status,
        is_public=plan.is_public,
        tier_type=plan.tier_type,
        version=plan.version,
        billing_interval=plan.billing_interval,
        currency=plan.currency,
        price_cents=plan.price_cents,
        metadata_json=plan.metadata_json or {},
        created_at=plan.created_at.isoformat() if plan.created_at else "",
        updated_at=plan.updated_at.isoformat() if plan.updated_at else "",
        published_at=plan.published_at.isoformat() if plan.published_at else None,
        retired_at=plan.retired_at.isoformat() if plan.retired_at else None,
        entitlements=ent_map,
    )


@router.get(
    "/plans",
    response_model=list[PlanResponse],
    summary="List all commercial plans",
    description="Retrieve catalog of commercial plans across all statuses (draft, active, retired) and versions.",
)
async def list_plans(
    plan_status: Optional[str] = Query(None, alias="status"),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PlanResponse]:
    _require_admin(user)

    stmt = select(CommercialPlanDB).order_by(CommercialPlanDB.plan_code, CommercialPlanDB.version.desc())
    if plan_status and isinstance(plan_status, str):
        stmt = stmt.where(CommercialPlanDB.status == plan_status)

    plans = (await session.execute(stmt)).scalars().all()

    result = []
    for p in plans:
        ent_stmt = select(CommercialPlanEntitlementDB).where(CommercialPlanEntitlementDB.plan_id == p.id)
        ents = (await session.execute(ent_stmt)).scalars().all()
        result.append(_format_plan_response(p, list(ents)))

    return result


@router.post(
    "/plans",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft commercial plan",
    description="Create a new draft plan with versioning and typed entitlements.",
)
async def create_plan(
    body: PlanCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlanResponse:
    _require_admin(user)

    # Determine highest version for plan_code
    v_stmt = select(func.coalesce(func.max(CommercialPlanDB.version), 0)).where(
        CommercialPlanDB.plan_code == body.plan_code
    )
    max_v = (await session.execute(v_stmt)).scalar() or 0
    next_v = max_v + 1

    now = _utcnow()
    plan_meta = dict(body.metadata_json or {})
    if body.tier_type == "paid" and body.price_cents is None:
        if "pricing_status" not in plan_meta:
            plan_meta["pricing_status"] = "unapproved_pending_commercial_review"

    plan = CommercialPlanDB(
        id=str(uuid.uuid4()),
        plan_code=body.plan_code,
        name=body.name,
        description=body.description,
        status="draft",
        is_public=body.is_public,
        tier_type=body.tier_type,
        version=next_v,
        billing_interval=body.billing_interval,
        currency=body.currency.upper(),
        price_cents=body.price_cents,
        metadata_json=plan_meta,
        created_at=now,
        updated_at=now,
    )
    session.add(plan)
    await session.flush()

    # Add entitlements
    created_ents = []
    provided_keys = set()
    for ent_in in body.entitlements:
        if ent_in.entitlement_key not in CANONICAL_ENTITLEMENT_METRICS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid entitlement key '{ent_in.entitlement_key}'",
            )
        ent = CommercialPlanEntitlementDB(
            id=str(uuid.uuid4()),
            plan_id=plan.id,
            entitlement_key=ent_in.entitlement_key,
            value_type=ent_in.value_type,
            value_bool=ent_in.value_bool,
            value_int=ent_in.value_int,
            is_unlimited=ent_in.is_unlimited,
            created_at=now,
        )
        session.add(ent)
        created_ents.append(ent)
        provided_keys.add(ent_in.entitlement_key)

    # Backfill missing canonical metrics with default values
    for k, spec in CANONICAL_ENTITLEMENT_METRICS.items():
        if k not in provided_keys:
            def_val = spec["default"]
            ent = CommercialPlanEntitlementDB(
                id=str(uuid.uuid4()),
                plan_id=plan.id,
                entitlement_key=k,
                value_type=spec["type"].value,
                value_bool=def_val if isinstance(def_val, bool) else None,
                value_int=def_val if isinstance(def_val, int) and not isinstance(def_val, bool) else None,
                is_unlimited=False,
                created_at=now,
            )
            session.add(ent)
            created_ents.append(ent)

    await log_commercial_audit(
        session,
        action="plan_created",
        actor_user_id=user.user_id,
        actor_role=user.role,
        reason=f"Created draft plan {plan.plan_code} v{plan.version}",
        details={"plan_id": plan.id, "plan_code": plan.plan_code, "version": plan.version},
    )

    await session.commit()
    return _format_plan_response(plan, created_ents)


@router.post(
    "/plans/{plan_id}/publish",
    response_model=PlanResponse,
    summary="Publish a draft commercial plan",
    description="Transitions plan to active status. Published plan versions become immutable to prevent silent contract alterations.",
)
async def publish_plan(
    plan_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlanResponse:
    _require_admin(user)

    plan = await session.get(CommercialPlanDB, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status == "active":
        raise HTTPException(status_code=400, detail="Plan is already active")
    if plan.status == "retired":
        raise HTTPException(status_code=400, detail="Retired plans cannot be republished directly; create a new version")

    # Commercial pricing validation (TASK 3)
    if plan.tier_type == "paid":
        if plan.price_cents is None or plan.price_cents <= 0:
            raise HTTPException(
                status_code=400,
                detail="Paid commercial plans cannot be published with unset or zero pricing. Please set an approved price.",
            )
        meta = plan.metadata_json or {}
        if meta.get("pricing_status") == "unapproved_pending_commercial_review":
            raise HTTPException(
                status_code=400,
                detail="Paid plan pricing is marked unapproved_pending_commercial_review. Commercial executive approval is required prior to publication.",
            )

    now = _utcnow()
    plan.status = "active"
    plan.published_at = now
    plan.updated_at = now

    ent_stmt = select(CommercialPlanEntitlementDB).where(CommercialPlanEntitlementDB.plan_id == plan.id)
    ents = (await session.execute(ent_stmt)).scalars().all()

    await log_commercial_audit(
        session,
        action="plan_published",
        actor_user_id=user.user_id,
        actor_role=user.role,
        reason=f"Published plan {plan.plan_code} v{plan.version}",
        details={"plan_id": plan.id, "plan_code": plan.plan_code, "version": plan.version, "price_cents": plan.price_cents},
    )

    await session.commit()
    return _format_plan_response(plan, list(ents))


@router.put(
    "/plans/{plan_id}/pricing",
    response_model=PlanResponse,
    summary="Update draft plan pricing and approval status",
    description="Set approved pricing for draft plans prior to publication. Published and retired plans are immutable.",
)
async def update_plan_pricing(
    plan_id: str,
    body: PlanPricingUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlanResponse:
    _require_admin(user)

    plan = await session.get(CommercialPlanDB, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot modify pricing on {plan.status} plan. Published or retired plans are immutable; create a new plan version.",
        )

    now = _utcnow()
    plan.price_cents = body.price_cents
    meta = dict(plan.metadata_json or {})
    meta["pricing_status"] = body.pricing_status
    meta["pricing_approved_by"] = user.user_id
    meta["pricing_approved_at"] = now.isoformat()
    plan.metadata_json = meta
    plan.updated_at = now

    ent_stmt = select(CommercialPlanEntitlementDB).where(CommercialPlanEntitlementDB.plan_id == plan.id)
    ents = (await session.execute(ent_stmt)).scalars().all()

    await log_commercial_audit(
        session,
        action="plan_pricing_updated",
        actor_user_id=user.user_id,
        actor_role=user.role,
        reason=body.reason,
        details={"plan_id": plan.id, "price_cents": body.price_cents, "pricing_status": body.pricing_status},
    )
    await session.commit()
    return _format_plan_response(plan, list(ents))


@router.post(
    "/plans/{plan_id}/retire",
    response_model=PlanResponse,
    summary="Retire an active commercial plan",
    description="Transitions plan to retired status. Existing tenant subscriptions remain honored, but no new assignments are permitted.",
)
async def retire_plan(
    plan_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlanResponse:
    _require_admin(user)

    plan = await session.get(CommercialPlanDB, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.status == "retired":
        raise HTTPException(status_code=400, detail="Plan is already retired")

    now = _utcnow()
    plan.status = "retired"
    plan.retired_at = now
    plan.updated_at = now

    ent_stmt = select(CommercialPlanEntitlementDB).where(CommercialPlanEntitlementDB.plan_id == plan.id)
    ents = (await session.execute(ent_stmt)).scalars().all()

    await log_commercial_audit(
        session,
        action="plan_retired",
        actor_user_id=user.user_id,
        actor_role=user.role,
        reason=f"Retired plan {plan.plan_code} v{plan.version}",
        details={"plan_id": plan.id, "plan_code": plan.plan_code, "version": plan.version},
    )

    await session.commit()
    return _format_plan_response(plan, list(ents))


@router.get(
    "/tenants/{tenant_id}/subscription",
    summary="Inspect tenant commercial subscription and effective entitlements",
    description="Retrieve comprehensive evaluation of effective limits, trial status, overrides and current consumption.",
)
async def inspect_tenant_subscription(
    tenant_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)

    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    status_data = await evaluate_tenant_quotas(session, tenant_id)
    return status_data


@router.post(
    "/tenants/{tenant_id}/assign-plan",
    summary="Assign a commercial plan to a tenant",
    description="Assign a published plan to a tenant with optional trial duration and audit justification.",
)
async def assign_tenant_plan(
    tenant_id: str,
    body: AssignPlanRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)

    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        sub = await assign_plan_to_tenant(
            session,
            tenant_id=tenant_id,
            plan_id=body.plan_id,
            status=body.status,
            trial_days=body.trial_days,
            actor_user_id=user.user_id,
            actor_role=user.role,
            reason=body.reason,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/extend-trial",
    summary="Extend a tenant's trial duration",
    description="Extend trial expiration date in UTC with mandatory audit reasoning.",
)
async def extend_trial(
    tenant_id: str,
    body: ExtendTrialRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)

    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        await extend_tenant_trial(
            session,
            tenant_id=tenant_id,
            additional_days=body.additional_days,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/overrides",
    summary="Configure tenant entitlement override",
    description="Set an explicit tenant-specific override taking precedence over the plan allowance with mandatory audit reason.",
)
async def set_override(
    tenant_id: str,
    body: EntitlementOverrideRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)

    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        await set_tenant_entitlement_override(
            session,
            tenant_id=tenant_id,
            entitlement_key=body.entitlement_key,
            value_type=body.value_type,
            value_bool=body.value_bool,
            value_int=body.value_int,
            is_unlimited=body.is_unlimited,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
            expires_at=body.expires_at,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.delete(
    "/tenants/{tenant_id}/overrides/{key}",
    summary="Revoke tenant entitlement override",
    description="Remove a tenant override and revert back to assigned plan allowance.",
)
async def delete_override(
    tenant_id: str,
    key: str,
    body: RevokeOverrideRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)

    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        revoked = await revoke_tenant_entitlement_override(
            session,
            tenant_id=tenant_id,
            entitlement_key=key,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        if not revoked:
            raise HTTPException(status_code=404, detail=f"Override for key '{key}' not found on tenant")
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.get(
    "/audit-logs",
    response_model=list[AuditLogResponse],
    summary="Query commercial audit logs",
    description="List immutable audit trail for commercial plan mutations, trial extensions, and overrides.",
)
async def list_audit_logs(
    tenant_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLogResponse]:
    _require_admin(user)

    stmt = select(CommercialAuditLogDB).order_by(desc(CommercialAuditLogDB.created_at))
    if isinstance(limit, int):
        stmt = stmt.limit(limit)
    else:
        stmt = stmt.limit(50)
    if tenant_id and isinstance(tenant_id, str):
        stmt = stmt.where(CommercialAuditLogDB.tenant_id == tenant_id)

    logs = (await session.execute(stmt)).scalars().all()
    return [
        AuditLogResponse(
            id=log.id,
            tenant_id=log.tenant_id,
            action=log.action,
            actor_user_id=log.actor_user_id,
            actor_role=log.actor_role,
            reason=log.reason,
            details_json=log.details_json or {},
            created_at=log.created_at.isoformat() if log.created_at else "",
        )
        for log in logs
    ]


@router.get(
    "/settings/trial",
    response_model=TrialSettingResponse,
    summary="Get persistent default trial setting",
    description="Retrieve system default trial duration for newly registered tenants from PostgreSQL app_settings.",
)
async def get_default_trial_setting(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TrialSettingResponse:
    _require_admin(user)
    from api.services.entitlement_service import get_system_default_trial_setting

    data = await get_system_default_trial_setting(session)
    return TrialSettingResponse(
        trial_days=data.get("trial_days", 14),
        description=data.get("description"),
        reason=data.get("reason"),
        updated_at=data.get("updated_at"),
        updated_by_user_id=data.get("updated_by_user_id"),
    )


@router.put(
    "/settings/trial",
    response_model=TrialSettingResponse,
    summary="Update persistent default trial setting",
    description="Persistently update default trial duration (0-365 days) with mandatory administrative audit reasoning.",
)
async def update_default_trial_setting(
    body: UpdateDefaultTrialRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TrialSettingResponse:
    _require_admin(user)
    from api.services.entitlement_service import update_system_default_trial_days

    try:
        updated = await update_system_default_trial_days(
            session,
            trial_days=body.trial_days,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return TrialSettingResponse(
        trial_days=updated["trial_days"],
        reason=updated.get("reason"),
        updated_at=updated.get("updated_at"),
        updated_by_user_id=user.user_id,
    )


@router.get(
    "/settings/enforcement",
    response_model=EnforcementSettingResponse,
    summary="Get commercial enforcement rollout configuration",
    description="Retrieve current enforcement mode ('disabled', 'shadow', 'enforce') and test tenant allowlist.",
)
async def get_enforcement_setting(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EnforcementSettingResponse:
    _require_admin(user)
    from api.services.enforcement_service import get_enforcement_config

    data = await get_enforcement_config(session)
    return EnforcementSettingResponse(
        mode=data["mode"],
        tenant_allowlist=data["tenant_allowlist"],
        description=data.get("description"),
        reason=data.get("reason"),
        updated_at=data.get("updated_at"),
        updated_by_user_id=data.get("updated_by_user_id"),
    )


@router.put(
    "/settings/enforcement",
    response_model=EnforcementSettingResponse,
    summary="Update commercial enforcement rollout configuration",
    description="Update enforcement mode ('disabled', 'shadow', 'enforce') and allowlist with mandatory audit trail.",
)
async def update_enforcement_setting(
    body: UpdateEnforcementSettingRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EnforcementSettingResponse:
    _require_admin(user)
    from api.services.enforcement_service import update_enforcement_config

    try:
        updated = await update_enforcement_config(
            session,
            mode=body.mode,
            tenant_allowlist=body.tenant_allowlist,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return EnforcementSettingResponse(
        mode=updated["mode"],
        tenant_allowlist=updated["tenant_allowlist"],
        reason=updated.get("reason"),
        updated_at=updated.get("updated_at"),
        updated_by_user_id=user.user_id,
    )


@router.get(
    "/reservations",
    response_model=list[ReservationResponse],
    summary="Inspect commercial reservations",
    description="List active, finalized, and released reservations with optional filtering and pagination.",
)
async def list_reservations(
    tenant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    operation_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ReservationResponse]:
    _require_admin(user)

    stmt = select(CommercialReservationDB).order_by(desc(CommercialReservationDB.created_at))
    if isinstance(limit, int):
        stmt = stmt.limit(limit)
    else:
        stmt = stmt.limit(50)

    if tenant_id and isinstance(tenant_id, str):
        stmt = stmt.where(CommercialReservationDB.tenant_id == tenant_id)
    if status and isinstance(status, str):
        stmt = stmt.where(CommercialReservationDB.status == status)
    if operation_type and isinstance(operation_type, str):
        stmt = stmt.where(CommercialReservationDB.operation_type == operation_type)

    rows = (await session.execute(stmt)).scalars().all()
    return [
        ReservationResponse(
            id=r.id,
            tenant_id=r.tenant_id,
            operation_type=r.operation_type,
            resource_id=r.resource_id,
            idempotency_key=r.idempotency_key,
            entitlement_key=r.entitlement_key,
            quantity=r.quantity,
            status=r.status,
            estimated_bytes=r.estimated_bytes,
            estimated_duration_seconds=r.estimated_duration_seconds,
            lease_expires_at=r.lease_expires_at.isoformat() if r.lease_expires_at else None,
            created_at=r.created_at.isoformat() if r.created_at else "",
            updated_at=r.updated_at.isoformat() if r.updated_at else "",
            finalized_at=r.finalized_at.isoformat() if r.finalized_at else None,
            released_at=r.released_at.isoformat() if r.released_at else None,
            metadata_json=r.metadata_json or {},
        )
        for r in rows
    ]


@router.post(
    "/reservations/reconcile",
    summary="Reconcile stale commercial reservations",
    description="Audit and reconcile reservations where lease has expired.",
)
async def trigger_reservation_reconciliation(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    from api.services.enforcement_service import reconcile_stale_reservations

    reconciled_count = await reconcile_stale_reservations(session)
    await session.commit()
    return {"success": True, "reconciled_count": reconciled_count}


# ── Subscription Lifecycle & Commercial Administration (STEP 04-A3) ──


@router.get(
    "/tenants/{tenant_id}/subscription/preview",
    response_model=PlanChangePreviewResponse,
    summary="Preview plan change effects on entitlements and pricing",
    description="Calculate entitlement diff, upgrade/downgrade direction, and pricing preview without modifying state.",
)
async def preview_subscription_change(
    tenant_id: str,
    target_plan_id: str = Query(..., description="Target commercial plan ID"),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlanChangePreviewResponse:
    _require_admin(user)
    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        preview = await preview_plan_change(session, tenant_id=tenant_id, target_plan_id=target_plan_id)
        return PlanChangePreviewResponse(**preview)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.post(
    "/tenants/{tenant_id}/subscription/assign",
    summary="Assign commercial plan immediately",
    description="Assign an approved published plan immediately to a tenant. Rejects draft and retired plans.",
)
async def assign_subscription_plan_endpoint(
    tenant_id: str,
    body: AssignPlanRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        await assign_tenant_plan_lifecycle(
            session,
            tenant_id=tenant_id,
            plan_id=body.plan_id,
            status=body.status,
            actor_user_id=user.user_id,
            actor_role=user.role,
            reason=body.reason or f"Administrative plan assignment by {user.user_id}",
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/schedule",
    response_model=ScheduledTransitionResponse,
    summary="Schedule future subscription transition",
    description="Schedule a future plan upgrade, downgrade, or cancellation at next period end or specific UTC timestamp.",
)
async def schedule_subscription_transition_endpoint(
    tenant_id: str,
    body: ScheduleTransitionRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScheduledTransitionResponse:
    _require_admin(user)
    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        record = await schedule_plan_transition(
            session,
            tenant_id=tenant_id,
            target_plan_id=body.target_plan_id,
            transition_type=body.transition_type,
            scheduled_at=body.scheduled_at,
            actor_user_id=user.user_id,
            actor_role=user.role,
            reason=body.reason,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    t_plan = None
    if record.target_plan_id:
        t_plan = await session.get(CommercialPlanDB, record.target_plan_id)

    return ScheduledTransitionResponse(
        id=record.id,
        tenant_id=record.tenant_id,
        target_plan_id=record.target_plan_id,
        target_plan_code=t_plan.plan_code if t_plan else None,
        target_plan_name=t_plan.name if t_plan else None,
        transition_type=record.transition_type,
        status=record.status,
        scheduled_at=record.scheduled_at.isoformat(),
        executed_at=record.executed_at.isoformat() if record.executed_at else None,
        reason=record.reason,
        actor_user_id=record.actor_user_id,
        actor_role=record.actor_role,
        metadata_json=record.metadata_json or {},
        created_at=record.created_at.isoformat() if record.created_at else "",
    )


@router.delete(
    "/tenants/{tenant_id}/subscription/schedule/{transition_id}",
    response_model=ScheduledTransitionResponse,
    summary="Cancel pending scheduled transition",
    description="Cancel an active pending plan transition and revert scheduled cancellation status.",
)
async def cancel_scheduled_transition_endpoint(
    tenant_id: str,
    transition_id: str,
    reason: str = Query("Cancelled by administrator", min_length=3),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScheduledTransitionResponse:
    _require_admin(user)
    try:
        record = await cancel_scheduled_transition(
            session,
            tenant_id=tenant_id,
            transition_id=transition_id,
            actor_user_id=user.user_id,
            actor_role=user.role,
            reason=reason,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    t_plan = None
    if record.target_plan_id:
        t_plan = await session.get(CommercialPlanDB, record.target_plan_id)

    return ScheduledTransitionResponse(
        id=record.id,
        tenant_id=record.tenant_id,
        target_plan_id=record.target_plan_id,
        target_plan_code=t_plan.plan_code if t_plan else None,
        target_plan_name=t_plan.name if t_plan else None,
        transition_type=record.transition_type,
        status=record.status,
        scheduled_at=record.scheduled_at.isoformat(),
        executed_at=record.executed_at.isoformat() if record.executed_at else None,
        reason=record.reason,
        actor_user_id=record.actor_user_id,
        actor_role=record.actor_role,
        metadata_json=record.metadata_json or {},
        created_at=record.created_at.isoformat() if record.created_at else "",
    )


@router.get(
    "/tenants/{tenant_id}/subscription/transitions",
    response_model=list[ScheduledTransitionResponse],
    summary="List tenant scheduled transitions",
    description="Retrieve pending, executed, and cancelled scheduled transitions for tenant.",
)
async def list_tenant_scheduled_transitions_endpoint(
    tenant_id: str,
    status: Optional[str] = Query(None, description="pending, executed, cancelled, failed"),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ScheduledTransitionResponse]:
    _require_admin(user)
    stmt = (
        select(ScheduledSubscriptionTransitionDB)
        .where(ScheduledSubscriptionTransitionDB.tenant_id == tenant_id)
        .order_by(desc(ScheduledSubscriptionTransitionDB.created_at))
    )
    if status:
        stmt = stmt.where(ScheduledSubscriptionTransitionDB.status == status)

    records = (await session.execute(stmt)).scalars().all()
    res = []
    for r in records:
        t_plan = None
        if r.target_plan_id:
            t_plan = await session.get(CommercialPlanDB, r.target_plan_id)
        res.append(
            ScheduledTransitionResponse(
                id=r.id,
                tenant_id=r.tenant_id,
                target_plan_id=r.target_plan_id,
                target_plan_code=t_plan.plan_code if t_plan else None,
                target_plan_name=t_plan.name if t_plan else None,
                transition_type=r.transition_type,
                status=r.status,
                scheduled_at=r.scheduled_at.isoformat(),
                executed_at=r.executed_at.isoformat() if r.executed_at else None,
                reason=r.reason,
                actor_user_id=r.actor_user_id,
                actor_role=r.actor_role,
                metadata_json=r.metadata_json or {},
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
        )
    return res


@router.post(
    "/plans/transitions/process-due",
    summary="Process due scheduled transitions",
    description="Idempotently execute pending scheduled plan transitions whose effective timestamp has arrived.",
)
async def trigger_due_scheduled_transitions_endpoint(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    executed = await process_due_subscription_transitions(session)
    await session.commit()
    return {"success": True, "executed_count": len(executed), "transitions": executed}


@router.post(
    "/tenants/{tenant_id}/subscription/grace-period",
    summary="Place tenant subscription into grace period",
    description="Grant temporary grace period extension (1-90 days) with mandatory administrative justification.",
)
async def enter_grace_period_endpoint(
    tenant_id: str,
    body: GracePeriodRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    try:
        await enter_grace_period(
            session,
            tenant_id=tenant_id,
            grace_days=body.grace_days,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/exit-grace",
    summary="Exit grace period",
    description="Exit grace period and transition to active, cancelled, or expired status.",
)
async def exit_grace_period_endpoint(
    tenant_id: str,
    body: ExitGracePeriodRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    try:
        await exit_grace_period(
            session,
            tenant_id=tenant_id,
            new_status=body.new_status,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/suspend",
    summary="Suspend tenant commercial access",
    description="Immediately suspend tenant commercial subscription and block future admissions.",
)
async def suspend_subscription_endpoint(
    tenant_id: str,
    body: SuspendSubscriptionRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    try:
        await suspend_subscription(
            session,
            tenant_id=tenant_id,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/unsuspend",
    summary="Unsuspend tenant commercial access",
    description="Lift commercial subscription suspension and restore previous active or trial state.",
)
async def unsuspend_subscription_endpoint(
    tenant_id: str,
    body: UnsuspendSubscriptionRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    try:
        await unsuspend_subscription(
            session,
            tenant_id=tenant_id,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/cancel",
    summary="Cancel tenant subscription",
    description="Cancel subscription immediately or schedule cancellation for period end.",
)
async def cancel_subscription_endpoint(
    tenant_id: str,
    body: CancelSubscriptionRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    try:
        await cancel_subscription(
            session,
            tenant_id=tenant_id,
            immediate=body.immediate,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/renew",
    summary="Administratively renew subscription period",
    description="Advance subscription period boundaries without fabricating payment records or invoices.",
)
async def renew_subscription_endpoint(
    tenant_id: str,
    body: RenewSubscriptionRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    try:
        await renew_subscription(
            session,
            tenant_id=tenant_id,
            interval=body.interval,
            custom_days=body.custom_days,
            payment_verification=body.payment_verification,
            reason=body.reason,
            actor_user_id=user.user_id,
            actor_role=user.role,
        )
        await session.commit()
    except ValueError as ve:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(ve))

    return await evaluate_tenant_quotas(session, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/quote",
    response_model=QuotationPreviewResponse,
    summary="Generate commercial quotation preview",
    description="Calculate quotation with approved pricing or 'Pricing not configured' for unapproved draft rates.",
)
async def preview_quotation_endpoint(
    tenant_id: str,
    body: QuotationPreviewRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> QuotationPreviewResponse:
    _require_admin(user)
    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        quote = await preview_commercial_quotation(
            session,
            tenant_id=tenant_id,
            plan_id=body.plan_id,
            custom_overrides=body.custom_overrides,
            discount_percent=body.discount_percent,
            notes=body.notes,
        )
        return QuotationPreviewResponse(**quote)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get(
    "/tenants/{tenant_id}/subscription/history",
    response_model=SubscriptionHistoryResponse,
    summary="Inspect tenant subscription history and audit events",
    description="Retrieve current subscription, pending and completed scheduled transitions, and commercial audit log.",
)
async def get_subscription_history_endpoint(
    tenant_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionHistoryResponse:
    _require_admin(user)
    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()

    current_sub_data = None
    if sub:
        plan = await session.get(CommercialPlanDB, sub.plan_id)
        current_sub_data = {
            "id": sub.id,
            "plan_id": sub.plan_id,
            "plan_code": plan.plan_code if plan else None,
            "plan_name": plan.name if plan else None,
            "status": sub.status,
            "trial_started_at": sub.trial_started_at.isoformat() if sub.trial_started_at else None,
            "trial_expires_at": sub.trial_expires_at.isoformat() if sub.trial_expires_at else None,
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
            "metadata_json": sub.metadata_json or {},
        }

    # Scheduled transitions
    tr_stmt = (
        select(ScheduledSubscriptionTransitionDB)
        .where(ScheduledSubscriptionTransitionDB.tenant_id == tenant_id)
        .order_by(desc(ScheduledSubscriptionTransitionDB.created_at))
    )
    transitions = (await session.execute(tr_stmt)).scalars().all()
    tr_responses = []
    for tr in transitions:
        tp = None
        if tr.target_plan_id:
            tp = await session.get(CommercialPlanDB, tr.target_plan_id)
        tr_responses.append(
            ScheduledTransitionResponse(
                id=tr.id,
                tenant_id=tr.tenant_id,
                target_plan_id=tr.target_plan_id,
                target_plan_code=tp.plan_code if tp else None,
                target_plan_name=tp.name if tp else None,
                transition_type=tr.transition_type,
                status=tr.status,
                scheduled_at=tr.scheduled_at.isoformat(),
                executed_at=tr.executed_at.isoformat() if tr.executed_at else None,
                reason=tr.reason,
                actor_user_id=tr.actor_user_id,
                actor_role=tr.actor_role,
                metadata_json=tr.metadata_json or {},
                created_at=tr.created_at.isoformat() if tr.created_at else "",
            )
        )

    # Commercial audit logs
    log_stmt = (
        select(CommercialAuditLogDB)
        .where(CommercialAuditLogDB.tenant_id == tenant_id)
        .order_by(desc(CommercialAuditLogDB.created_at))
        .limit(100)
    )
    logs = (await session.execute(log_stmt)).scalars().all()
    audit_responses = [
        AuditLogResponse(
            id=l.id,
            tenant_id=l.tenant_id,
            action=l.action,
            actor_user_id=l.actor_user_id,
            actor_role=l.actor_role,
            reason=l.reason,
            details_json=l.details_json or {},
            created_at=l.created_at.isoformat() if l.created_at else "",
        )
        for l in logs
    ]

    return SubscriptionHistoryResponse(
        tenant_id=tenant_id,
        current_subscription=current_sub_data,
        scheduled_transitions=tr_responses,
        audit_events=audit_responses,
    )


@router.get(
    "/tenants/{tenant_id}/overrides",
    summary="List tenant entitlement overrides",
    description="Retrieve all active and expired entitlement overrides configured for tenant.",
)
async def list_tenant_overrides_endpoint(
    tenant_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)
    tenant = await session.get(TenantDB, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    stmt = select(TenantEntitlementOverrideDB).where(TenantEntitlementOverrideDB.tenant_id == tenant_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "entitlement_key": r.entitlement_key,
            "value_type": r.value_type,
            "value_bool": r.value_bool,
            "value_int": r.value_int,
            "is_unlimited": r.is_unlimited,
            "reason": r.reason,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]


# ── Super Admin Dashboard, Tenant Directory, Transitions & Scheduler Observability (STEP 04-B1) ──


@router.get(
    "/dashboard",
    response_model=DashboardStatsResponse,
    summary="Commercial Super Admin Dashboard Statistics",
    description="Retrieve aggregated platform statistics for commercial operations.",
)
async def get_commercial_dashboard(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DashboardStatsResponse:
    _require_admin(user)
    from api.services.enforcement_service import get_enforcement_config

    total_tenants = (await session.execute(select(func.count()).select_from(TenantDB))).scalar() or 0

    status_counts_res = await session.execute(
        select(TenantSubscriptionDB.status, func.count()).group_by(TenantSubscriptionDB.status)
    )
    status_counts = dict(status_counts_res.all())

    tier_counts_res = await session.execute(
        select(CommercialPlanDB.tier_type, func.count())
        .select_from(TenantSubscriptionDB)
        .join(CommercialPlanDB, TenantSubscriptionDB.plan_id == CommercialPlanDB.id)
        .group_by(CommercialPlanDB.tier_type)
    )
    tier_counts = dict(tier_counts_res.all())

    pending_tr = (await session.execute(
        select(func.count()).select_from(ScheduledSubscriptionTransitionDB)
        .where(ScheduledSubscriptionTransitionDB.status == "pending")
    )).scalar() or 0

    active_plans = (await session.execute(
        select(func.count()).select_from(CommercialPlanDB)
        .where(CommercialPlanDB.status == "active")
    )).scalar() or 0

    enforce_cfg = await get_enforcement_config(session)
    sched_cfg = await get_scheduler_config(session)
    sched_state = await get_scheduler_state(session)

    return DashboardStatsResponse(
        total_tenants=total_tenants,
        free_tenants=tier_counts.get("free", 0),
        trial_tenants=status_counts.get("trial", 0),
        paid_tenants=tier_counts.get("paid", 0),
        legacy_tenants=status_counts.get("legacy_active", 0),
        suspended_tenants=status_counts.get("suspended", 0),
        grace_period_tenants=status_counts.get("grace_period", 0),
        pending_transitions=pending_tr,
        active_plans=active_plans,
        enforcement=enforce_cfg,
        scheduler={
            **sched_cfg,
            **sched_state,
        },
    )


@router.get(
    "/tenants",
    response_model=TenantListResponse,
    summary="List tenants with commercial subscription details",
    description="Search and filter tenants for commercial operations.",
)
async def list_tenants_endpoint(
    search: Optional[str] = Query(None, description="Search by name, slug, or ID"),
    status: Optional[str] = Query(None, description="Filter by subscription status"),
    tier: Optional[str] = Query(None, description="Filter by plan tier"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TenantListResponse:
    _require_admin(user)

    stmt = select(TenantDB).outerjoin(
        TenantSubscriptionDB, TenantDB.id == TenantSubscriptionDB.tenant_id
    ).outerjoin(
        CommercialPlanDB, TenantSubscriptionDB.plan_id == CommercialPlanDB.id
    )

    count_stmt = select(func.count()).select_from(TenantDB).outerjoin(
        TenantSubscriptionDB, TenantDB.id == TenantSubscriptionDB.tenant_id
    ).outerjoin(
        CommercialPlanDB, TenantSubscriptionDB.plan_id == CommercialPlanDB.id
    )

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(TenantDB.name).like(term),
            func.lower(TenantDB.slug).like(term),
            TenantDB.id.like(term),
        ))
        count_stmt = count_stmt.where(or_(
            func.lower(TenantDB.name).like(term),
            func.lower(TenantDB.slug).like(term),
            TenantDB.id.like(term),
        ))

    if status and status.strip():
        stmt = stmt.where(TenantSubscriptionDB.status == status.strip())
        count_stmt = count_stmt.where(TenantSubscriptionDB.status == status.strip())

    if tier and tier.strip():
        stmt = stmt.where(CommercialPlanDB.tier_type == tier.strip())
        count_stmt = count_stmt.where(CommercialPlanDB.tier_type == tier.strip())

    total = (await session.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(TenantDB.created_at.desc()).limit(limit).offset(offset)
    tenants = (await session.execute(stmt)).scalars().all()

    now = _utcnow()
    items = []
    for t in tenants:
        sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == t.id)
        sub = (await session.execute(sub_stmt)).scalars().first()

        plan = None
        if sub and sub.plan_id:
            plan = await session.get(CommercialPlanDB, sub.plan_id)

        tr_stmt = select(func.count()).select_from(ScheduledSubscriptionTransitionDB).where(
            ScheduledSubscriptionTransitionDB.tenant_id == t.id,
            ScheduledSubscriptionTransitionDB.status == "pending",
        )
        has_pending = ((await session.execute(tr_stmt)).scalar() or 0) > 0

        trial_days_remaining = None
        if sub and sub.trial_expires_at:
            if sub.trial_expires_at > now:
                trial_days_remaining = max(0, (sub.trial_expires_at - now).days)
            else:
                trial_days_remaining = 0

        grace_days_remaining = None
        grace_expires_at = None
        if sub and sub.metadata_json and sub.metadata_json.get("grace_period"):
            gp = sub.metadata_json["grace_period"]
            if gp.get("grace_period_expires_at"):
                exp_dt = datetime.fromisoformat(gp["grace_period_expires_at"])
                grace_expires_at = exp_dt.isoformat()
                grace_days_remaining = max(0, (exp_dt - now).days) if exp_dt > now else 0

        is_suspended = False
        suspension_reason = None
        if sub and sub.status == "suspended":
            is_suspended = True
            if sub.metadata_json and sub.metadata_json.get("suspension"):
                suspension_reason = sub.metadata_json["suspension"].get("reason")

        pv_state = "none"
        if sub and sub.metadata_json:
            pv_state = sub.metadata_json.get("payment_verification_state", "none")

        items.append(
            TenantListItemResponse(
                tenant_id=t.id,
                name=t.name,
                slug=t.slug,
                tier=t.tier or "basic",
                created_at=t.created_at.isoformat() if t.created_at else "",
                plan_id=plan.id if plan else None,
                plan_code=plan.plan_code if plan else None,
                plan_name=plan.name if plan else None,
                tier_type=plan.tier_type if plan else None,
                subscription_status=sub.status if sub else "unknown",
                current_period_start=sub.current_period_start.isoformat() if sub and sub.current_period_start else None,
                current_period_end=sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
                trial_days_remaining=trial_days_remaining,
                trial_expires_at=sub.trial_expires_at.isoformat() if sub and sub.trial_expires_at else None,
                grace_period_remaining_days=grace_days_remaining,
                grace_period_expires_at=grace_expires_at,
                is_suspended=is_suspended,
                suspension_reason=suspension_reason,
                payment_verification_state=pv_state,
                has_pending_transition=has_pending,
            )
        )

    return TenantListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/transitions",
    response_model=ScheduledTransitionListResponse,
    summary="List all scheduled transitions across tenants",
    description="Retrieve all transitions with optional status filtering.",
)
async def list_all_transitions(
    status: Optional[str] = Query(None, description="pending, executed, cancelled, failed"),
    tenant_id: Optional[str] = Query(None, description="Optional tenant ID filter"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ScheduledTransitionListResponse:
    _require_admin(user)

    stmt = select(ScheduledSubscriptionTransitionDB)
    count_stmt = select(func.count()).select_from(ScheduledSubscriptionTransitionDB)

    if status and status.strip():
        stmt = stmt.where(ScheduledSubscriptionTransitionDB.status == status.strip())
        count_stmt = count_stmt.where(ScheduledSubscriptionTransitionDB.status == status.strip())

    if tenant_id and tenant_id.strip():
        stmt = stmt.where(ScheduledSubscriptionTransitionDB.tenant_id == tenant_id.strip())
        count_stmt = count_stmt.where(ScheduledSubscriptionTransitionDB.tenant_id == tenant_id.strip())

    total = (await session.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(ScheduledSubscriptionTransitionDB.scheduled_at.desc()).limit(limit).offset(offset)
    transitions = (await session.execute(stmt)).scalars().all()

    items = []
    for tr in transitions:
        plan = None
        if tr.target_plan_id:
            plan = await session.get(CommercialPlanDB, tr.target_plan_id)
        items.append(
            ScheduledTransitionResponse(
                id=tr.id,
                tenant_id=tr.tenant_id,
                target_plan_id=tr.target_plan_id,
                target_plan_code=plan.plan_code if plan else None,
                target_plan_name=plan.name if plan else None,
                transition_type=tr.transition_type,
                status=tr.status,
                scheduled_at=tr.scheduled_at.isoformat() if tr.scheduled_at else "",
                executed_at=tr.executed_at.isoformat() if tr.executed_at else None,
                reason=tr.reason,
                actor_user_id=tr.actor_user_id,
                actor_role=tr.actor_role,
                metadata_json=tr.metadata_json or {},
                created_at=tr.created_at.isoformat() if tr.created_at else "",
            )
        )

    return ScheduledTransitionListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/scheduler/status",
    response_model=SchedulerStatusResponse,
    summary="Get background transition scheduler status and metrics",
    description="Retrieve live status, configuration, and health metrics for the automatic transition processor.",
)
async def get_scheduler_status_endpoint(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SchedulerStatusResponse:
    _require_admin(user)

    cfg = await get_scheduler_config(session)
    state = await get_scheduler_state(session)
    now = _utcnow()

    pending_due_count = (await session.execute(
        select(func.count()).select_from(ScheduledSubscriptionTransitionDB)
        .where(
            ScheduledSubscriptionTransitionDB.status == "pending",
            ScheduledSubscriptionTransitionDB.scheduled_at <= now,
        )
    )).scalar() or 0

    total_pending_count = (await session.execute(
        select(func.count()).select_from(ScheduledSubscriptionTransitionDB)
        .where(ScheduledSubscriptionTransitionDB.status == "pending")
    )).scalar() or 0

    failed_count = (await session.execute(
        select(func.count()).select_from(ScheduledSubscriptionTransitionDB)
        .where(ScheduledSubscriptionTransitionDB.status == "failed")
    )).scalar() or 0

    return SchedulerStatusResponse(
        enabled=cfg.get("enabled", True),
        paused=cfg.get("paused", False),
        rollout_mode=cfg.get("rollout_mode", "allowlist_only"),
        test_tenant_prefixes=cfg.get("test_tenant_prefixes", []),
        tenant_allowlist=cfg.get("tenant_allowlist", []),
        poll_interval_seconds=cfg.get("poll_interval_seconds", 60),
        last_scan_at=state.get("last_scan_at"),
        last_executed_count=state.get("last_executed_count", 0),
        worker_id=state.get("worker_id"),
        cluster_status=state.get("cluster_status", "paused" if cfg.get("paused") else "idle"),
        active_worker_count=state.get("active_worker_count", 1),
        last_successful_scan_at=state.get("last_successful_scan_at"),
        last_failed_scan_at=state.get("last_failed_scan_at"),
        pending_due_count=pending_due_count,
        total_pending_count=total_pending_count,
        failed_count=failed_count,
        updated_at=cfg.get("updated_at"),
    )


@router.post(
    "/scheduler/pause",
    response_model=SchedulerStatusResponse,
    summary="Pause automatic transition scheduler",
    description="Emergency control to pause automated transition execution.",
)
async def pause_scheduler_endpoint(
    request: PauseSchedulerRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SchedulerStatusResponse:
    _require_admin(user)

    await update_scheduler_config(
        session,
        paused=True,
        reason=request.reason,
        actor_user_id=user.user_id,
        actor_role=user.role,
    )
    await session.commit()
    return await get_scheduler_status_endpoint(user=user, session=session)


@router.post(
    "/scheduler/resume",
    response_model=SchedulerStatusResponse,
    summary="Resume automatic transition scheduler",
    description="Resume automated transition processing following pause.",
)
async def resume_scheduler_endpoint(
    request: ResumeSchedulerRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SchedulerStatusResponse:
    _require_admin(user)

    await update_scheduler_config(
        session,
        paused=False,
        reason=request.reason,
        actor_user_id=user.user_id,
        actor_role=user.role,
    )
    await session.commit()
    return await get_scheduler_status_endpoint(user=user, session=session)


@router.post(
    "/scheduler/trigger",
    response_model=TriggerSchedulerResponse,
    summary="Manually trigger immediate due transition scan",
    description="Forces immediate execution of all currently due transitions.",
)
async def trigger_scheduler_scan(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TriggerSchedulerResponse:
    _require_admin(user)

    results = await process_due_subscription_transitions(session, enforce_rollout_guard=False)
    await session.commit()
    return TriggerSchedulerResponse(
        executed_count=len(results),
        executed_transitions=results,
        message=f"Successfully processed {len(results)} due subscription transitions",
    )


@router.post(
    "/scheduler/retry-failed",
    summary="Reset failed transitions to pending",
    description="Administrative recovery to re-queue transitions that encountered unexpected errors.",
)
async def retry_failed_transitions_endpoint(
    request: RetryFailedTransitionsRequest,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _require_admin(user)

    count = await retry_failed_transitions(
        session,
        reason=request.reason,
        actor_user_id=user.user_id,
        actor_role=user.role,
    )
    await session.commit()
    return {
        "status": "success",
        "reset_count": count,
        "message": f"Reset {count} failed scheduled transitions back to pending status",
    }
