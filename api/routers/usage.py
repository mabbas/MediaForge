"""Usage tracking, commercial API metering, and advisory quota endpoints (STEP 02-E2B)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.database.models import UserDB
from api.dependencies import CurrentUser, get_current_user, get_usage_tracker, require_scope
from api.schemas.usage import (
    CommercialUsageSummaryResponse,
    QuotaItem,
    TenantQuotasResponse,
    UsageEventCreate,
    UsageEventListResponse,
    UsageEventResponse,
    UsageSummaryResponse,
)
from api.services.usage_service import (
    get_tenant_advisory_quotas,
    get_tenant_usage_events,
    get_tenant_usage_summary,
    record_usage_event,
)
from src.features.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/usage", tags=["Usage"])


def _resolve_target_tenant(
    user: CurrentUser,
    target_tenant_id: str | None,
) -> str:
    """Enforce tenant isolation for usage queries.

    Only system operators/admins may query a foreign tenant ID.
    Tenant admins and members are strictly bound to their authenticated tenant.
    """
    if target_tenant_id and target_tenant_id != user.tenant_id:
        if not (user.is_system_admin or user.is_system_owner):
            raise HTTPException(
                status_code=403,
                detail="Access denied: Cannot query foreign tenant usage",
            )
        return target_tenant_id
    return user.tenant_id


@router.get(
    "",
    response_model=UsageSummaryResponse,
    summary="Usage summary (Legacy & User)",
    description="Get current usage counters and remaining quotas for the authenticated user.",
)
async def get_usage(
    user: CurrentUser = Depends(require_scope("usage:read")),
    tracker: UsageTracker = Depends(get_usage_tracker),
    session: AsyncSession = Depends(get_session),
):
    """Get current usage with advisory limits."""
    today_usage = tracker.get_all_usage(user.user_id)
    limits = {}
    counters = ["daily_downloads", "batch_size", "playlist_size"]
    for counter in counters:
        used = tracker.get_usage(user.user_id, counter)
        remaining = tracker.get_remaining(user.user_id, counter, tier=user.tier)
        limit_val = -1
        if remaining != -1:
            limit_val = used + remaining
        limits[counter] = {
            "used": used,
            "limit": limit_val,
            "remaining": remaining,
        }
    return UsageSummaryResponse(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        tier=user.tier,
        today=today_usage,
        limits=limits,
    )


@router.get(
    "/summary",
    response_model=CommercialUsageSummaryResponse,
    summary="Commercial Tenant Usage Summary",
    description="Get aggregated consumption metrics across downloads, clips, merges and transcripts.",
)
async def get_commercial_usage_summary(
    target_tenant_id: str | None = Query(None, description="System operator override for tenant ID"),
    user_id: str | None = Query(None, description="Filter metrics by specific user ID"),
    period: str = Query("all_time", description="Time period: today, month, all_time"),
    user: CurrentUser = Depends(require_scope("usage:read")),
    session: AsyncSession = Depends(get_session),
):
    """Retrieve tenant-scoped commercial usage summary."""
    effective_tenant_id = _resolve_target_tenant(user, target_tenant_id)

    # Member role is restricted to their own user usage
    effective_user_id = user_id
    if user.role == "member":
        effective_user_id = user.user_id

    # Compute period bounds
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_date = None
    if period == "today":
        start_date = datetime(now.year, now.month, now.day)
    elif period == "month":
        start_date = datetime(now.year, now.month, 1)

    summary_data = await get_tenant_usage_summary(
        session,
        tenant_id=effective_tenant_id,
        user_id=effective_user_id,
        start_date=start_date,
        end_date=now,
        period=period,
    )
    return CommercialUsageSummaryResponse(
        success=True,
        **summary_data,
    )


@router.get(
    "/events",
    response_model=UsageEventListResponse,
    summary="List Usage Events Ledger",
    description="Get paginated metered usage events for the tenant with optional filters.",
)
async def list_usage_events(
    target_tenant_id: str | None = Query(None, description="System operator override for tenant ID"),
    event_type: str | None = Query(None, description="Filter by event type (download, clip, merge, transcription)"),
    user_id: str | None = Query(None, description="Filter by user ID"),
    api_key_id: str | None = Query(None, description="Filter by API key ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    user: CurrentUser = Depends(require_scope("usage:read")),
    session: AsyncSession = Depends(get_session),
):
    """Retrieve tenant-scoped usage events ledger."""
    effective_tenant_id = _resolve_target_tenant(user, target_tenant_id)

    effective_user_id = user_id
    if user.role == "member":
        effective_user_id = user.user_id

    events, total = await get_tenant_usage_events(
        session,
        tenant_id=effective_tenant_id,
        event_type=event_type,
        user_id=effective_user_id,
        api_key_id=api_key_id,
        page=page,
        page_size=page_size,
    )

    event_responses = [
        UsageEventResponse(
            id=e.id,
            tenant_id=e.tenant_id,
            user_id=e.user_id,
            api_key_id=e.api_key_id,
            event_type=e.event_type,
            idempotency_key=e.idempotency_key,
            resource_id=e.resource_id,
            quantity=e.quantity,
            unit=e.unit,
            bytes=e.bytes,
            duration_seconds=e.duration_seconds,
            auth_method=e.auth_method,
            client_ip=e.client_ip,
            metadata_json=e.metadata_json or {},
            created_at=e.created_at.isoformat() if e.created_at else "",
        )
        for e in events
    ]

    return UsageEventListResponse(
        success=True,
        tenant_id=effective_tenant_id,
        total=total,
        page=page,
        page_size=page_size,
        events=event_responses,
    )


@router.get(
    "/quotas",
    response_model=TenantQuotasResponse,
    summary="Advisory Quota Report",
    description="Evaluate current tenant usage against quotas (defaults to UNLIMITED -1).",
)
async def get_advisory_quotas(
    target_tenant_id: str | None = Query(None, description="System operator override for tenant ID"),
    period: str = Query("monthly", description="Quota period: monthly, daily"),
    user: CurrentUser = Depends(require_scope("usage:read")),
    session: AsyncSession = Depends(get_session),
):
    """Retrieve tenant advisory quota evaluation."""
    effective_tenant_id = _resolve_target_tenant(user, target_tenant_id)

    quota_data = await get_tenant_advisory_quotas(
        session,
        tenant_id=effective_tenant_id,
        period=period,
    )
    return TenantQuotasResponse(
        success=True,
        tenant_id=quota_data["tenant_id"],
        tier=quota_data["tier"],
        period=quota_data["period"],
        quotas=quota_data["quotas"],
    )


@router.post(
    "/events",
    response_model=UsageEventResponse,
    status_code=201,
    summary="Record Usage Event (Internal System Workers Only)",
    description="Record a metered usage event with database-enforced idempotency. Restricted to internal system workers.",
)
async def create_usage_event(
    body: UsageEventCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Record an authoritative usage event. Fails closed for ordinary customers or API keys."""
    # Fail-closed trust boundary: Ordinary customer principals (JWT or commercial API keys)
    # cannot inject arbitrary authoritative usage events. Only verified internal system workers are allowed.
    if not user.is_system_admin:
        raise HTTPException(
            status_code=403,
            detail="Direct usage event ingestion is restricted to authorized internal system workers",
        )

    from api.services.usage_service import IdempotencyConflictError

    client_ip = request.client.host if request.client else None
    try:
        event, is_new = await record_usage_event(
            session,
            tenant_id=user.tenant_id,
            event_type=body.event_type,
            idempotency_key=body.idempotency_key,
            resource_id=body.resource_id,
            quantity=body.quantity,
            unit=body.unit,
            bytes=body.bytes,
            duration_seconds=body.duration_seconds,
            user_id=user.user_id,
            api_key_id=user.api_key_id,
            auth_method=user.auth_method.value if hasattr(user.auth_method, "value") else str(user.auth_method),
            client_ip=client_ip,
            metadata_json=body.metadata_json,
        )
        await session.commit()
    except IdempotencyConflictError as err:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=str(err),
        ) from err

    return UsageEventResponse(
        id=event.id,
        tenant_id=event.tenant_id,
        user_id=event.user_id,
        api_key_id=event.api_key_id,
        event_type=event.event_type,
        idempotency_key=event.idempotency_key,
        resource_id=event.resource_id,
        quantity=event.quantity,
        unit=event.unit,
        bytes=event.bytes,
        duration_seconds=event.duration_seconds,
        auth_method=event.auth_method,
        client_ip=event.client_ip,
        metadata_json=event.metadata_json or {},
        created_at=event.created_at.isoformat() if event.created_at else "",
    )


@router.post(
    "/reset",
    response_model=dict,
    summary="Reset usage",
    description="Reset usage counters for user in current tenant (admin only).",
)
async def reset_usage(
    target_user_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
    tracker: UsageTracker = Depends(get_usage_tracker),
    session: AsyncSession = Depends(get_session),
):
    """Reset usage counters with tenant isolation check."""
    if not user.is_admin_or_owner:
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required",
        )

    user_to_reset = user.user_id
    if target_user_id:
        target = await session.get(UserDB, target_user_id)
        if not target or target.tenant_id != user.tenant_id:
            raise HTTPException(
                status_code=404,
                detail="User not found",
            )
        user_to_reset = target.id

    tracker.reset(user_to_reset)
    return {
        "success": True,
        "message": f"Usage reset for {user_to_reset}",
    }


@router.get(
    "/subscription",
    summary="Commercial Plan and Advisory Limits Visibility (STEP 04-A1)",
    description="Retrieve current commercial plan details, trial status, reset dates, and advisory usage meters for the authenticated tenant.",
)
async def get_tenant_commercial_subscription(
    target_tenant_id: str | None = Query(None, description="System operator override for tenant ID"),
    user: CurrentUser = Depends(require_scope("usage:read")),
    session: AsyncSession = Depends(get_session),
):
    """Return authenticated tenant's plan info, trial expiration, reset dates, and advisory usage meters."""
    effective_tenant_id = _resolve_target_tenant(user, target_tenant_id)
    from api.services.entitlement_service import evaluate_tenant_quotas

    status_data = await evaluate_tenant_quotas(session, effective_tenant_id)
    return status_data


class SimulateOperationRequest(BaseModel):
    operation_type: str = Query(..., description="download, clip_extract, merge, etc.")
    estimated_bytes: int = 0
    estimated_duration_seconds: float = 0.0


@router.post(
    "/simulate",
    summary="Simulate Prospective Operation Quotas (STEP 04-A1)",
    description="Advisory read-only check to simulate if a requested operation would fit within allowances.",
)
async def simulate_quota_admission(
    body: SimulateOperationRequest,
    target_tenant_id: str | None = Query(None, description="System operator override for tenant ID"),
    user: CurrentUser = Depends(require_scope("usage:read")),
    session: AsyncSession = Depends(get_session),
):
    """Advisory check simulating quota admission."""
    effective_tenant_id = _resolve_target_tenant(user, target_tenant_id)
    from api.services.entitlement_service import simulate_operation_admission

    sim_result = await simulate_operation_admission(
        session,
        tenant_id=effective_tenant_id,
        operation_type=body.operation_type,
        estimated_bytes=body.estimated_bytes,
        estimated_duration_seconds=body.estimated_duration_seconds,
    )
    return sim_result