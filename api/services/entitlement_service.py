"""Commercial Plan Catalog, Tenant Entitlements, Trial Configuration, and Quota Evaluation Service (STEP 04-A1).

This service implements:
1. Typed entitlement resolution following strict precedence:
   Tenant Override -> Assigned Plan Version -> System Default Tier
2. UTC-based configurable trial management and administrative trial extension.
3. Commercial audit trail recording for plan, trial, and entitlement mutations.
4. Read-only advisory quota evaluation (STRICTLY ADVISORY, zero hard customer blocking).
5. Storage and concurrency measurement.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.models import (
    AppSettingDB,
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

logger = logging.getLogger(__name__)

DEFAULT_TRIAL_SETTING_KEY = "commercial:default_trial_days"


# ── Typed Entitlement Units & Enums ──


class EntitlementType(str, Enum):
    BOOLEAN = "boolean"
    COUNT = "count"
    BYTES = "bytes"
    SECONDS = "seconds"
    CONCURRENCY = "concurrency"


class SubscriptionStatus(str, Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    GRACE_PERIOD = "grace_period"
    SCHEDULED_CANCELLATION = "scheduled_cancellation"
    LEGACY_ACTIVE = "legacy_active"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    PENDING_ACTIVATION = "pending_activation"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class QuotaReasonCode(str, Enum):
    QUOTA_OK = "QUOTA_OK"
    FEATURE_NOT_INCLUDED = "FEATURE_NOT_INCLUDED"
    CONCURRENCY_EXCEEDED = "CONCURRENCY_EXCEEDED"
    DOWNLOAD_COUNT_EXCEEDED = "DOWNLOAD_COUNT_EXCEEDED"
    DOWNLOAD_BYTES_EXCEEDED = "DOWNLOAD_BYTES_EXCEEDED"
    CLIP_COUNT_EXCEEDED = "CLIP_COUNT_EXCEEDED"
    CLIP_DURATION_EXCEEDED = "CLIP_DURATION_EXCEEDED"
    MERGE_COUNT_EXCEEDED = "MERGE_COUNT_EXCEEDED"
    STORAGE_EXCEEDED = "STORAGE_EXCEEDED"
    API_KEY_LIMIT_EXCEEDED = "API_KEY_LIMIT_EXCEEDED"
    TRIAL_EXPIRED = "TRIAL_EXPIRED"
    SUBSCRIPTION_CANCELLED = "SUBSCRIPTION_CANCELLED"
    SUBSCRIPTION_SUSPENDED = "SUBSCRIPTION_SUSPENDED"
    SUBSCRIPTION_PENDING_ACTIVATION = "SUBSCRIPTION_PENDING_ACTIVATION"


# Canonical Entitlements Catalog
CANONICAL_ENTITLEMENT_METRICS: dict[str, dict[str, Any]] = {
    "desktop_access": {"type": EntitlementType.BOOLEAN, "unit": "boolean", "default": True},
    "api_access": {"type": EntitlementType.BOOLEAN, "unit": "boolean", "default": False},
    "max_active_api_keys": {"type": EntitlementType.COUNT, "unit": "count", "default": 0},
    "monthly_download_count": {"type": EntitlementType.COUNT, "unit": "count", "default": 50},
    "monthly_download_bytes": {"type": EntitlementType.BYTES, "unit": "bytes", "default": 5 * 1024 * 1024 * 1024},  # 5GB
    "monthly_clip_count": {"type": EntitlementType.COUNT, "unit": "count", "default": 20},
    "monthly_clip_seconds": {"type": EntitlementType.SECONDS, "unit": "seconds", "default": 600},  # 10m
    "monthly_merge_count": {"type": EntitlementType.COUNT, "unit": "count", "default": 10},
    "max_concurrent_jobs": {"type": EntitlementType.CONCURRENCY, "unit": "concurrency", "default": 1},
    "storage_bytes": {"type": EntitlementType.BYTES, "unit": "bytes", "default": 5 * 1024 * 1024 * 1024},  # 5GB
    "artifact_retention_days": {"type": EntitlementType.COUNT, "unit": "days", "default": 7},
    "ai_transcription_access": {"type": EntitlementType.BOOLEAN, "unit": "boolean", "default": False},
    "max_users": {"type": EntitlementType.COUNT, "unit": "count", "default": 1},
}


def _utcnow() -> datetime:
    """Standardized UTC timestamp without timezone offset for database consistency."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Audit Trail Service ──


async def log_commercial_audit(
    session: AsyncSession,
    *,
    action: str,
    tenant_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
    reason: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> CommercialAuditLogDB:
    """Record an immutable audit log entry for commercial subscription and entitlement changes."""
    log_entry = CommercialAuditLogDB(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        action=action,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason,
        details_json=details or {},
        created_at=_utcnow(),
    )
    session.add(log_entry)
    return log_entry


# ── System Default Trial Configuration (STEP 04-A1-Fix-A) ──


async def get_system_default_trial_days(session: AsyncSession) -> int:
    """Retrieve persistent default trial duration in days from PostgreSQL app_settings (fallback: 14)."""
    stmt = select(AppSettingDB).where(AppSettingDB.key == DEFAULT_TRIAL_SETTING_KEY)
    rec = (await session.execute(stmt)).scalars().first()
    if rec and isinstance(rec.value, dict):
        val = rec.value.get("trial_days")
        if isinstance(val, int) and 0 <= val <= 365:
            return val
    return 14


async def get_system_default_trial_setting(session: AsyncSession) -> dict[str, Any]:
    """Retrieve full persistent trial setting payload including metadata and audit details."""
    stmt = select(AppSettingDB).where(AppSettingDB.key == DEFAULT_TRIAL_SETTING_KEY)
    rec = (await session.execute(stmt)).scalars().first()
    if rec and isinstance(rec.value, dict):
        data = dict(rec.value)
        data["updated_at"] = rec.updated_at.isoformat() if rec.updated_at else None
        return data
    return {
        "trial_days": 14,
        "description": "Default trial duration in days for newly activated commercial tenants",
        "reason": "System default initial baseline",
        "updated_at": None,
    }


async def update_system_default_trial_days(
    session: AsyncSession,
    *,
    trial_days: int,
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> dict[str, Any]:
    """Persistently update system default trial duration with mandatory audit reasoning."""
    if trial_days < 0 or trial_days > 365:
        raise ValueError("trial_days must be between 0 and 365 days")
    if not reason or not reason.strip():
        raise ValueError("Mandatory administrative reason is required to update default trial duration")

    stmt = select(AppSettingDB).where(AppSettingDB.key == DEFAULT_TRIAL_SETTING_KEY)
    rec = (await session.execute(stmt)).scalars().first()

    old_val = rec.value.get("trial_days") if (rec and isinstance(rec.value, dict)) else 14
    now = _utcnow()

    new_payload = {
        "trial_days": trial_days,
        "description": "Default trial duration in days for newly activated commercial tenants",
        "updated_by_user_id": actor_user_id,
        "reason": reason.strip(),
        "updated_at": now.isoformat(),
    }

    if not rec:
        rec = AppSettingDB(
            key=DEFAULT_TRIAL_SETTING_KEY,
            value=new_payload,
            updated_at=now,
        )
        session.add(rec)
    else:
        rec.value = new_payload
        rec.updated_at = now

    await log_commercial_audit(
        session,
        action="default_trial_days_updated",
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={
            "previous_trial_days": old_val,
            "new_trial_days": trial_days,
        },
    )

    await session.flush()
    return {
        "trial_days": trial_days,
        "previous_trial_days": old_val,
        "updated_at": now.isoformat(),
        "reason": reason.strip(),
    }


# ── Entitlement Resolution ──


class EffectiveEntitlement(BaseModel):
    key: str
    value_type: str
    value_bool: Optional[bool] = None
    value_int: Optional[int] = None
    is_unlimited: bool = False
    source: str  # "override", "plan", "default"
    override_reason: Optional[str] = None
    override_expires_at: Optional[datetime] = None


async def resolve_effective_entitlements(
    session: AsyncSession,
    tenant_id: str,
) -> tuple[dict[str, EffectiveEntitlement], Optional[TenantSubscriptionDB], Optional[CommercialPlanDB]]:
    """Resolve effective typed entitlements for a tenant following strict precedence:

    1. Active Tenant Override (if not expired)
    2. Assigned Plan Version
    3. Documented System Default Tier
    """
    now = _utcnow()

    # 1. Fetch Subscription & Plan
    sub_stmt = (
        select(TenantSubscriptionDB)
        .where(TenantSubscriptionDB.tenant_id == tenant_id)
    )
    sub = (await session.execute(sub_stmt)).scalars().first()

    plan: Optional[CommercialPlanDB] = None
    plan_entitlements: dict[str, CommercialPlanEntitlementDB] = {}

    if sub:
        plan_stmt = select(CommercialPlanDB).where(CommercialPlanDB.id == sub.plan_id)
        plan = (await session.execute(plan_stmt)).scalars().first()
        if plan:
            ent_stmt = select(CommercialPlanEntitlementDB).where(
                CommercialPlanEntitlementDB.plan_id == plan.id
            )
            ents = (await session.execute(ent_stmt)).scalars().all()
            plan_entitlements = {e.entitlement_key: e for e in ents}

    # 2. Fetch Active Overrides
    override_stmt = select(TenantEntitlementOverrideDB).where(
        TenantEntitlementOverrideDB.tenant_id == tenant_id
    )
    overrides = (await session.execute(override_stmt)).scalars().all()
    active_overrides: dict[str, TenantEntitlementOverrideDB] = {}
    for ov in overrides:
        if ov.expires_at is None or ov.expires_at > now:
            active_overrides[ov.entitlement_key] = ov

    # 3. Resolve all canonical keys
    resolved: dict[str, EffectiveEntitlement] = {}
    for key, spec in CANONICAL_ENTITLEMENT_METRICS.items():
        vtype = spec["type"].value

        # Precedence 1: Tenant Override
        if key in active_overrides:
            ov = active_overrides[key]
            resolved[key] = EffectiveEntitlement(
                key=key,
                value_type=ov.value_type,
                value_bool=ov.value_bool,
                value_int=ov.value_int,
                is_unlimited=ov.is_unlimited,
                source="override",
                override_reason=ov.reason,
                override_expires_at=ov.expires_at,
            )
            continue

        # Precedence 2: Assigned Plan Version
        if key in plan_entitlements:
            pe = plan_entitlements[key]
            resolved[key] = EffectiveEntitlement(
                key=key,
                value_type=pe.value_type,
                value_bool=pe.value_bool,
                value_int=pe.value_int,
                is_unlimited=pe.is_unlimited,
                source="plan",
            )
            continue

        # Precedence 3: Documented System Default Tier
        def_val = spec["default"]
        vbool = def_val if isinstance(def_val, bool) else None
        vint = def_val if isinstance(def_val, int) and not isinstance(def_val, bool) else None
        resolved[key] = EffectiveEntitlement(
            key=key,
            value_type=vtype,
            value_bool=vbool,
            value_int=vint,
            is_unlimited=False,
            source="default",
        )

    return resolved, sub, plan


# ── Quota & Concurrency Evaluation ──


class QuotaCheckResult(BaseModel):
    metric: str
    unit: str
    allowed: bool
    current_value: float
    limit_value: Optional[int]  # None if unlimited
    remaining_value: Optional[float]  # None if unlimited
    is_unlimited: bool
    is_exceeded: bool
    reason_code: QuotaReasonCode
    message: str


class TenantCommercialStatusResponse(BaseModel):
    tenant_id: str
    plan_code: str
    plan_name: str
    plan_version: int
    tier_type: str
    subscription_status: str  # trial, active, grace_period, scheduled_cancellation, cancelled, expired, suspended, unassigned
    is_trial: bool
    trial_started_at: Optional[str] = None
    trial_expires_at: Optional[str] = None
    trial_days_remaining: Optional[int] = None
    grace_period_expires_at: Optional[str] = None
    grace_period_remaining_days: Optional[int] = None
    scheduled_transition: Optional[dict[str, Any]] = None
    is_suspended: bool = False
    suspension_reason: Optional[str] = None
    current_period_start: str
    current_period_end: Optional[str] = None
    period_reset_at: Optional[str] = None
    is_advisory: bool = True  # In STEP 04-A1, all quotas are strictly advisory
    checks: dict[str, QuotaCheckResult]
    consumption: dict[str, Any]
    effective_entitlements: dict[str, dict[str, Any]]


async def get_tenant_current_period_usage(
    session: AsyncSession,
    tenant_id: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Calculate measured consumption for a tenant within the current billing period."""
    # 1. Downloads, clips, merges, transcripts in period
    filters = [
        UsageEventDB.tenant_id == tenant_id,
        UsageEventDB.created_at >= period_start,
        UsageEventDB.created_at <= period_end,
    ]

    stmt = (
        select(
            UsageEventDB.event_type,
            func.count(UsageEventDB.id).label("count"),
            func.coalesce(func.sum(UsageEventDB.bytes), 0).label("bytes"),
            func.coalesce(func.sum(UsageEventDB.duration_seconds), 0.0).label("duration_seconds"),
            func.coalesce(func.sum(UsageEventDB.quantity), 0.0).label("total_quantity"),
        )
        .where(*filters)
        .group_by(UsageEventDB.event_type)
    )
    rows = (await session.execute(stmt)).all()

    breakdown: dict[str, dict[str, Any]] = {}
    for r in rows:
        breakdown[r.event_type] = {
            "count": int(r.count),
            "bytes": int(r.bytes),
            "duration_seconds": float(r.duration_seconds),
            "quantity": float(r.total_quantity),
        }

    dl = breakdown.get("download", {})
    cl = breakdown.get("clip", {})
    mg = breakdown.get("merge", {})
    tr = breakdown.get("transcription", {})

    # 2. Retained Storage Bytes (Authoritative Active Artifacts & Downloads)
    storage_stmt = (
        select(func.coalesce(func.sum(MediaArtifactDB.file_size_bytes), 0))
        .where(
            MediaArtifactDB.tenant_id == tenant_id,
            MediaArtifactDB.status == "ready",
        )
    )
    art_storage_bytes = (await session.execute(storage_stmt)).scalar() or 0

    dl_storage_stmt = (
        select(func.coalesce(func.sum(DownloadJobDB.file_size_bytes), 0))
        .where(
            DownloadJobDB.tenant_id == tenant_id,
            DownloadJobDB.status == "completed",
        )
    )
    dl_storage_bytes = (await session.execute(dl_storage_stmt)).scalar() or 0
    total_storage_bytes = int(art_storage_bytes + dl_storage_bytes)

    # 3. Authoritative Concurrency Metrics (STEP 04-A2-Fix-A)
    c_metrics = await get_authoritative_concurrency_metrics(session, tenant_id)

    return {
        "monthly_download_count": dl.get("count", 0),
        "monthly_download_bytes": dl.get("bytes", 0),
        "monthly_clip_count": cl.get("count", 0),
        "monthly_clip_seconds": cl.get("duration_seconds", 0.0),
        "monthly_merge_count": mg.get("count", 0),
        "monthly_transcription_minutes": round(tr.get("duration_seconds", 0.0) / 60.0, 2),
        "storage_bytes": total_storage_bytes,
        "artifact_storage_bytes": int(art_storage_bytes),
        "download_storage_bytes": int(dl_storage_bytes),
        "max_concurrent_jobs": c_metrics["admitted_outstanding_operations"],
        "queued_jobs": c_metrics["queued_jobs"],
        "running_jobs": c_metrics["running_jobs"],
        "reserved_operations": c_metrics["reserved_operations"],
        "active_processing_operations": c_metrics["active_processing_operations"],
        "legacy_unreserved_operations": c_metrics["legacy_unreserved_operations"],
        "admitted_outstanding_operations": c_metrics["admitted_outstanding_operations"],
        "global_running_jobs": c_metrics["global_worker_processing_capacity"],
        "global_worker_processing_capacity": c_metrics["global_worker_processing_capacity"],
    }


async def get_authoritative_concurrency_metrics(
    session: AsyncSession,
    tenant_id: str,
) -> dict[str, int]:
    """Calculate authoritative tenant concurrency metrics with zero double-counting.

    Invariant:
        Each admitted logical operation consumes exactly ONE outstanding slot.
        - Active reservations (reserved or active) hold 1 slot each.
        - Legacy jobs without an active reservation hold 1 slot each.
        - Succeeded, failed, cancelled, and released operations hold 0 slots.
    """
    # 1. Commercial Reservations by status
    res_stmt = select(func.count(CommercialReservationDB.id)).where(
        CommercialReservationDB.tenant_id == tenant_id,
        CommercialReservationDB.status == "reserved",
    )
    reserved_operations = int((await session.execute(res_stmt)).scalar() or 0)

    act_stmt = select(func.count(CommercialReservationDB.id)).where(
        CommercialReservationDB.tenant_id == tenant_id,
        CommercialReservationDB.status == "active",
    )
    active_processing_operations = int((await session.execute(act_stmt)).scalar() or 0)

    # 2. Work items breakdown for progress & worker tracking
    queued_stmt = select(func.count(MediaWorkItemDB.id)).where(
        MediaWorkItemDB.tenant_id == tenant_id,
        MediaWorkItemDB.status == "queued",
    )
    queued_jobs = int((await session.execute(queued_stmt)).scalar() or 0)

    running_stmt = select(func.count(MediaWorkItemDB.id)).where(
        MediaWorkItemDB.tenant_id == tenant_id,
        MediaWorkItemDB.status == "running",
    )
    running_jobs = int((await session.execute(running_stmt)).scalar() or 0)

    # 3. Active reservation keys and resource_ids for deduplicating legacy jobs
    active_res_subq_res_id = (
        select(CommercialReservationDB.resource_id)
        .where(
            CommercialReservationDB.tenant_id == tenant_id,
            CommercialReservationDB.status.in_(["reserved", "active"]),
            CommercialReservationDB.resource_id.isnot(None),
        )
    )
    active_res_subq_idem = (
        select(CommercialReservationDB.idempotency_key)
        .where(
            CommercialReservationDB.tenant_id == tenant_id,
            CommercialReservationDB.status.in_(["reserved", "active"]),
        )
    )

    # Legacy unreserved work items
    legacy_wi_stmt = select(func.count(MediaWorkItemDB.id)).where(
        MediaWorkItemDB.tenant_id == tenant_id,
        MediaWorkItemDB.status.in_(["queued", "running", "retry_wait"]),
        ~MediaWorkItemDB.id.in_(active_res_subq_res_id),
        ~MediaWorkItemDB.idempotency_key.in_(active_res_subq_idem),
    )
    legacy_wi = int((await session.execute(legacy_wi_stmt)).scalar() or 0)

    # Legacy unreserved download jobs
    legacy_dl_stmt = select(func.count(DownloadJobDB.id)).where(
        DownloadJobDB.tenant_id == tenant_id,
        DownloadJobDB.status.in_(["pending", "queued", "downloading"]),
        ~DownloadJobDB.id.in_(active_res_subq_res_id),
    )
    legacy_dl = int((await session.execute(legacy_dl_stmt)).scalar() or 0)

    legacy_unreserved_operations = legacy_wi + legacy_dl

    # Total authoritative admitted outstanding operations
    admitted_outstanding_operations = (
        reserved_operations + active_processing_operations + legacy_unreserved_operations
    )

    # Global worker capacity
    global_running_stmt = select(func.count(MediaWorkItemDB.id)).where(
        MediaWorkItemDB.status == "running"
    )
    global_worker_processing_capacity = int((await session.execute(global_running_stmt)).scalar() or 0)

    return {
        "queued_jobs": queued_jobs,
        "running_jobs": running_jobs,
        "reserved_operations": reserved_operations,
        "active_processing_operations": active_processing_operations,
        "legacy_unreserved_operations": legacy_unreserved_operations,
        "admitted_outstanding_operations": admitted_outstanding_operations,
        "global_worker_processing_capacity": global_worker_processing_capacity,
    }


async def evaluate_tenant_quotas(
    session: AsyncSession,
    tenant_id: str,
) -> TenantCommercialStatusResponse:
    """Evaluate tenant entitlements against actual consumption.

    All evaluations in STEP 04-A1 are strictly advisory (no hard customer blocking).
    """
    now = _utcnow()
    entitlements, sub, plan = await resolve_effective_entitlements(session, tenant_id)

    # Resolve subscription details
    sub_status = sub.status if sub else "unassigned"
    is_trial = sub_status == "trial"
    trial_start = sub.trial_started_at if sub else None
    trial_exp = sub.trial_expires_at if sub else None
    trial_days_remaining = None

    if is_trial and trial_exp:
        diff = trial_exp - now
        trial_days_remaining = max(0, diff.days + (1 if diff.seconds > 0 else 0))
        if trial_exp < now:
            sub_status = "expired"

    # Grace period and suspension evaluation
    grace_exp_str = None
    grace_remaining_days = None
    is_suspended = False
    suspension_reason = None

    sub_meta = (sub.metadata_json or {}) if sub else {}
    if sub_status == "grace_period":
        grace_data = sub_meta.get("grace_period", {})
        if "expires_at" in grace_data:
            grace_exp_str = grace_data["expires_at"]
            try:
                g_exp = datetime.fromisoformat(grace_exp_str)
                if g_exp < now:
                    sub_status = "expired"
                else:
                    g_diff = g_exp - now
                    grace_remaining_days = max(0, g_diff.days + (1 if g_diff.seconds > 0 else 0))
            except Exception:
                pass
    elif sub_status == "suspended":
        is_suspended = True
        suspension_reason = sub_meta.get("suspension", {}).get("reason")

    # Pending scheduled transition
    from api.database.models import ScheduledSubscriptionTransitionDB
    sched_stmt = (
        select(ScheduledSubscriptionTransitionDB)
        .where(
            ScheduledSubscriptionTransitionDB.tenant_id == tenant_id,
            ScheduledSubscriptionTransitionDB.status == "pending",
        )
        .order_by(ScheduledSubscriptionTransitionDB.scheduled_at.asc())
    )
    pending_sched = (await session.execute(sched_stmt)).scalars().first()
    sched_info = None
    if pending_sched:
        t_plan = None
        if pending_sched.target_plan_id:
            t_plan = await session.get(CommercialPlanDB, pending_sched.target_plan_id)
        sched_info = {
            "id": pending_sched.id,
            "transition_type": pending_sched.transition_type,
            "scheduled_at": pending_sched.scheduled_at.isoformat(),
            "target_plan_id": pending_sched.target_plan_id,
            "target_plan_code": t_plan.plan_code if t_plan else None,
            "target_plan_name": t_plan.name if t_plan else None,
            "reason": pending_sched.reason,
        }

    # Resolve Period Bounds
    period_start = sub.current_period_start if sub else datetime(now.year, now.month, 1)
    # Default monthly period reset: first of next month
    if now.month == 12:
        period_reset = datetime(now.year + 1, 1, 1)
    else:
        period_reset = datetime(now.year, now.month + 1, 1)
    period_end = sub.current_period_end if (sub and sub.current_period_end) else period_reset

    # Gather consumption
    consumption = await get_tenant_current_period_usage(session, tenant_id, period_start, now)

    # Perform Quota & Entitlement Checks
    checks: dict[str, QuotaCheckResult] = {}

    def _eval_numeric_metric(metric_key: str, reason_exceeded: QuotaReasonCode, unit: str):
        ent = entitlements.get(metric_key)
        used = float(consumption.get(metric_key, 0))
        if not ent or ent.is_unlimited:
            checks[metric_key] = QuotaCheckResult(
                metric=metric_key,
                unit=unit,
                allowed=True,
                current_value=used,
                limit_value=None,
                remaining_value=None,
                is_unlimited=True,
                is_exceeded=False,
                reason_code=QuotaReasonCode.QUOTA_OK,
                message=f"{metric_key} allowance is unlimited",
            )
            return

        limit = ent.value_int or 0
        is_exceeded = used >= limit
        remaining = max(0.0, float(limit - used))
        reason = reason_exceeded if is_exceeded else QuotaReasonCode.QUOTA_OK
        msg = f"Exceeded {metric_key} limit: {int(used)}/{limit}" if is_exceeded else f"{metric_key} within limit: {int(used)}/{limit}"

        checks[metric_key] = QuotaCheckResult(
            metric=metric_key,
            unit=unit,
            allowed=not is_exceeded,
            current_value=used,
            limit_value=limit,
            remaining_value=remaining,
            is_unlimited=False,
            is_exceeded=is_exceeded,
            reason_code=reason,
            message=msg,
        )

    # Boolean Checks
    for bool_key in ["desktop_access", "api_access", "ai_transcription_access"]:
        ent = entitlements.get(bool_key)
        val = ent.value_bool if ent else False
        checks[bool_key] = QuotaCheckResult(
            metric=bool_key,
            unit="boolean",
            allowed=bool(val),
            current_value=1.0 if val else 0.0,
            limit_value=1 if val else 0,
            remaining_value=1.0 if val else 0.0,
            is_unlimited=False,
            is_exceeded=not bool(val),
            reason_code=QuotaReasonCode.QUOTA_OK if val else QuotaReasonCode.FEATURE_NOT_INCLUDED,
            message=f"{bool_key} is {'included' if val else 'not included in plan'}",
        )

    # Numeric Checks
    _eval_numeric_metric("max_concurrent_jobs", QuotaReasonCode.CONCURRENCY_EXCEEDED, "concurrency")
    _eval_numeric_metric("monthly_download_count", QuotaReasonCode.DOWNLOAD_COUNT_EXCEEDED, "count")
    _eval_numeric_metric("monthly_download_bytes", QuotaReasonCode.DOWNLOAD_BYTES_EXCEEDED, "bytes")
    _eval_numeric_metric("monthly_clip_count", QuotaReasonCode.CLIP_COUNT_EXCEEDED, "count")
    _eval_numeric_metric("monthly_clip_seconds", QuotaReasonCode.CLIP_DURATION_EXCEEDED, "seconds")
    _eval_numeric_metric("monthly_merge_count", QuotaReasonCode.MERGE_COUNT_EXCEEDED, "count")
    _eval_numeric_metric("storage_bytes", QuotaReasonCode.STORAGE_EXCEEDED, "bytes")

    # API Keys Limit Check
    key_ent = entitlements.get("max_active_api_keys")
    from api.database.models import APIKeyDB

    active_keys_stmt = select(func.count(APIKeyDB.id)).where(
        APIKeyDB.tenant_id == tenant_id,
        APIKeyDB.is_active == True,
        APIKeyDB.is_revoked == False,
    )
    active_keys_count = (await session.execute(active_keys_stmt)).scalar() or 0
    consumption["max_active_api_keys"] = active_keys_count

    key_limit = key_ent.value_int if (key_ent and not key_ent.is_unlimited) else None
    key_exceeded = (active_keys_count >= key_limit) if key_limit is not None else False
    checks["max_active_api_keys"] = QuotaCheckResult(
        metric="max_active_api_keys",
        unit="count",
        allowed=not key_exceeded,
        current_value=float(active_keys_count),
        limit_value=key_limit,
        remaining_value=max(0.0, float(key_limit - active_keys_count)) if key_limit is not None else None,
        is_unlimited=key_ent.is_unlimited if key_ent else False,
        is_exceeded=key_exceeded,
        reason_code=QuotaReasonCode.API_KEY_LIMIT_EXCEEDED if key_exceeded else QuotaReasonCode.QUOTA_OK,
        message=f"Active API keys: {active_keys_count}/{key_limit if key_limit is not None else 'unlimited'}",
    )

    # Prepare effective entitlements summary dictionary
    effective_summary = {
        k: {
            "value_type": v.value_type,
            "value_bool": v.value_bool,
            "value_int": v.value_int,
            "is_unlimited": v.is_unlimited,
            "source": v.source,
            "override_reason": v.override_reason,
            "override_expires_at": v.override_expires_at.isoformat() if v.override_expires_at else None,
        }
        for k, v in entitlements.items()
    }

    return TenantCommercialStatusResponse(
        tenant_id=tenant_id,
        plan_code=plan.plan_code if plan else "default_free",
        plan_name=plan.name if plan else "MediaForge Free Default",
        plan_version=plan.version if plan else 1,
        tier_type=plan.tier_type if plan else "free",
        subscription_status=sub_status,
        is_trial=is_trial,
        trial_started_at=trial_start.isoformat() if trial_start else None,
        trial_expires_at=trial_exp.isoformat() if trial_exp else None,
        trial_days_remaining=trial_days_remaining,
        grace_period_expires_at=grace_exp_str,
        grace_period_remaining_days=grace_remaining_days,
        scheduled_transition=sched_info,
        is_suspended=is_suspended,
        suspension_reason=suspension_reason,
        current_period_start=period_start.isoformat(),
        current_period_end=period_end.isoformat() if period_end else None,
        period_reset_at=period_reset.isoformat(),
        is_advisory=True,
        checks=checks,
        consumption=consumption,
        effective_entitlements=effective_summary,
    )


async def simulate_operation_admission(
    session: AsyncSession,
    *,
    tenant_id: str,
    operation_type: str,
    estimated_bytes: int = 0,
    estimated_duration_seconds: float = 0.0,
) -> dict[str, Any]:
    """Simulate commercial quota admission for a prospective operation (read-only advisory)."""
    status = await evaluate_tenant_quotas(session, tenant_id)

    violations: list[str] = []
    reason_codes: list[str] = []

    # Check status
    if status.subscription_status == "suspended":
        violations.append("Tenant commercial subscription is suspended")
        reason_codes.append(QuotaReasonCode.SUBSCRIPTION_SUSPENDED.value)
    elif status.subscription_status in ("expired", "cancelled"):
        violations.append(f"Subscription is currently {status.subscription_status}")
        reason_codes.append(
            QuotaReasonCode.TRIAL_EXPIRED.value
            if status.subscription_status == "expired"
            else QuotaReasonCode.SUBSCRIPTION_CANCELLED.value
        )
    elif status.subscription_status == "pending_activation":
        violations.append("Tenant commercial subscription is pending activation")
        reason_codes.append(QuotaReasonCode.SUBSCRIPTION_PENDING_ACTIVATION.value)

    # Check concurrency
    c_check = status.checks.get("max_concurrent_jobs")
    if c_check and not c_check.is_unlimited:
        if c_check.current_value >= (c_check.limit_value or 0):
            violations.append(
                f"Maximum concurrent background jobs reached ({int(c_check.current_value)}/{c_check.limit_value})"
            )
            reason_codes.append(QuotaReasonCode.CONCURRENCY_EXCEEDED.value)

    # Check operation specific limits
    if operation_type == "download":
        dl_cnt = status.checks.get("monthly_download_count")
        if dl_cnt and not dl_cnt.is_unlimited and (dl_cnt.remaining_value or 0) <= 0:
            violations.append("Monthly download count quota exhausted")
            reason_codes.append(QuotaReasonCode.DOWNLOAD_COUNT_EXCEEDED.value)

        dl_bytes = status.checks.get("monthly_download_bytes")
        if dl_bytes and not dl_bytes.is_unlimited:
            if (dl_bytes.remaining_value or 0) < estimated_bytes:
                violations.append("Estimated download size exceeds remaining monthly byte quota")
                reason_codes.append(QuotaReasonCode.DOWNLOAD_BYTES_EXCEEDED.value)

    elif operation_type in ("clip_extract", "clip"):
        cl_cnt = status.checks.get("monthly_clip_count")
        if cl_cnt and not cl_cnt.is_unlimited and (cl_cnt.remaining_value or 0) <= 0:
            violations.append("Monthly clip extraction count quota exhausted")
            reason_codes.append(QuotaReasonCode.CLIP_COUNT_EXCEEDED.value)

        cl_sec = status.checks.get("monthly_clip_seconds")
        if cl_sec and not cl_sec.is_unlimited:
            if (cl_sec.remaining_value or 0) < estimated_duration_seconds:
                violations.append("Estimated clip duration exceeds remaining monthly transcode seconds")
                reason_codes.append(QuotaReasonCode.CLIP_DURATION_EXCEEDED.value)

    elif operation_type in ("merge", "clips_merge"):
        mg_cnt = status.checks.get("monthly_merge_count")
        if mg_cnt and not mg_cnt.is_unlimited and (mg_cnt.remaining_value or 0) <= 0:
            violations.append("Monthly clip merge quota exhausted")
            reason_codes.append(QuotaReasonCode.MERGE_COUNT_EXCEEDED.value)

    # Storage Check
    st_check = status.checks.get("storage_bytes")
    if st_check and not st_check.is_unlimited:
        if (st_check.remaining_value or 0) < estimated_bytes:
            violations.append("Projected output exceeds remaining persistent storage limit")
            reason_codes.append(QuotaReasonCode.STORAGE_EXCEEDED.value)

    admissible = len(violations) == 0
    return {
        "tenant_id": tenant_id,
        "operation_type": operation_type,
        "admissible": admissible,
        "is_advisory": True,
        "violations": violations,
        "reason_codes": reason_codes or [QuotaReasonCode.QUOTA_OK.value],
        "projected_concurrency": int(status.consumption.get("max_concurrent_jobs", 0) + 1),
        "plan_code": status.plan_code,
        "subscription_status": status.subscription_status,
    }


# ── Administrative Plan & Trial Management ──


async def assign_plan_to_tenant(
    session: AsyncSession,
    *,
    tenant_id: str,
    plan_id: str,
    status: str = "active",
    trial_days: Optional[int] = None,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
    reason: Optional[str] = None,
) -> TenantSubscriptionDB:
    """Assign a published commercial plan to a tenant with audit trail."""
    plan = await session.get(CommercialPlanDB, plan_id)
    if not plan:
        raise ValueError(f"Plan ID '{plan_id}' does not exist")
    if plan.status == "retired":
        raise ValueError(f"Plan '{plan.plan_code}' (v{plan.version}) is retired and cannot accept new assignments")
    if plan.status == "draft":
        raise ValueError(f"Plan '{plan.plan_code}' (v{plan.version}) is draft and cannot be assigned until published")

    now = _utcnow()
    trial_start = None
    trial_exp = None
    if status == "trial" or plan.tier_type == "trial":
        status = "trial"
        days = trial_days if trial_days is not None else int(plan.metadata_json.get("trial_duration_days", 14))
        trial_start = now
        trial_exp = now + timedelta(days=days)

    # Fetch existing subscription or create new
    stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(stmt)).scalars().first()

    old_plan_id = sub.plan_id if sub else None
    old_status = sub.status if sub else None

    if not sub:
        sub = TenantSubscriptionDB(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            plan_id=plan_id,
            status=status,
            trial_started_at=trial_start,
            trial_expires_at=trial_exp,
            current_period_start=now,
            created_at=now,
            updated_at=now,
        )
        session.add(sub)
    else:
        sub.plan_id = plan_id
        sub.status = status
        sub.trial_started_at = trial_start
        sub.trial_expires_at = trial_exp
        sub.updated_at = now

    await log_commercial_audit(
        session,
        action="tenant_plan_assigned",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason or f"Assigned plan {plan.plan_code} v{plan.version}",
        details={
            "old_plan_id": old_plan_id,
            "new_plan_id": plan_id,
            "old_status": old_status,
            "new_status": status,
            "plan_code": plan.plan_code,
            "plan_version": plan.version,
            "trial_expires_at": trial_exp.isoformat() if trial_exp else None,
        },
    )

    await session.flush()
    return sub


async def extend_tenant_trial(
    session: AsyncSession,
    *,
    tenant_id: str,
    additional_days: int,
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> TenantSubscriptionDB:
    """Extend a tenant's trial duration with mandatory audit logging and reason."""
    if additional_days <= 0:
        raise ValueError("additional_days must be greater than 0")
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for trial extensions")

    stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(stmt)).scalars().first()
    if not sub:
        raise ValueError(f"Tenant '{tenant_id}' has no subscription record to extend")

    now = _utcnow()
    # Baseline for extension: current expiry if in future, else now
    baseline = sub.trial_expires_at if (sub.trial_expires_at and sub.trial_expires_at > now) else now
    new_expiry = baseline + timedelta(days=additional_days)
    old_expiry = sub.trial_expires_at

    sub.trial_expires_at = new_expiry
    sub.status = "trial"
    sub.updated_at = now

    await log_commercial_audit(
        session,
        action="trial_extended",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={
            "additional_days": additional_days,
            "old_expires_at": old_expiry.isoformat() if old_expiry else None,
            "new_expires_at": new_expiry.isoformat(),
        },
    )

    await session.flush()
    return sub


async def set_tenant_entitlement_override(
    session: AsyncSession,
    *,
    tenant_id: str,
    entitlement_key: str,
    value_type: str,
    reason: str,
    value_bool: Optional[bool] = None,
    value_int: Optional[int] = None,
    is_unlimited: bool = False,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
    expires_at: Optional[datetime] = None,
) -> TenantEntitlementOverrideDB:
    """Configure an explicit entitlement override for a tenant with mandatory audit reason."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for entitlement overrides")
    if entitlement_key not in CANONICAL_ENTITLEMENT_METRICS:
        raise ValueError(f"Unknown entitlement key '{entitlement_key}'")

    now = _utcnow()
    stmt = select(TenantEntitlementOverrideDB).where(
        TenantEntitlementOverrideDB.tenant_id == tenant_id,
        TenantEntitlementOverrideDB.entitlement_key == entitlement_key,
    )
    override = (await session.execute(stmt)).scalars().first()

    if not override:
        override = TenantEntitlementOverrideDB(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            entitlement_key=entitlement_key,
            value_type=value_type,
            value_bool=value_bool,
            value_int=value_int,
            is_unlimited=is_unlimited,
            reason=reason.strip(),
            granted_by_user_id=actor_user_id,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        session.add(override)
    else:
        override.value_type = value_type
        override.value_bool = value_bool
        override.value_int = value_int
        override.is_unlimited = is_unlimited
        override.reason = reason.strip()
        override.granted_by_user_id = actor_user_id
        override.expires_at = expires_at
        override.updated_at = now

    await log_commercial_audit(
        session,
        action="entitlement_override_set",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={
            "entitlement_key": entitlement_key,
            "value_type": value_type,
            "value_bool": value_bool,
            "value_int": value_int,
            "is_unlimited": is_unlimited,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )

    await session.flush()
    return override


async def revoke_tenant_entitlement_override(
    session: AsyncSession,
    *,
    tenant_id: str,
    entitlement_key: str,
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> bool:
    """Revoke a tenant-specific entitlement override with audit trail."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for override revocation")

    stmt = select(TenantEntitlementOverrideDB).where(
        TenantEntitlementOverrideDB.tenant_id == tenant_id,
        TenantEntitlementOverrideDB.entitlement_key == entitlement_key,
    )
    override = (await session.execute(stmt)).scalars().first()
    if not override:
        return False

    await session.delete(override)

    await log_commercial_audit(
        session,
        action="entitlement_override_revoked",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={"entitlement_key": entitlement_key},
    )

    await session.flush()
    return True
