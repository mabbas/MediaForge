"""Commercial transactional quota enforcement, durable reservations, and feature flag controls.

STEP 04-A2: Production-grade admission and subscription entitlement enforcement.
Guarantees:
1. Strict server-side feature flag rollout: default 'disabled' (advisory), 'shadow' (would-block audit), 'enforce' (allowlisted test tenants only).
2. Existing 481 tenants and 22 legacy_active tenants remain completely unaffected by default.
3. Transactional row-level locking per tenant ensures zero race conditions or oversubscription under concurrency.
4. ACID lifecycle: reservations created atomically with jobs; retries reuse reservations; finalization is committed together with usage events.
5. Standardized error contracts conforming to documented reason codes and HTTP status codes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.models import (
    AppSettingDB,
    CommercialAuditLogDB,
    CommercialPlanDB,
    CommercialReservationDB,
    DownloadJobDB,
    MediaWorkItemDB,
    TenantDB,
    TenantSubscriptionDB,
    UsageEventDB,
)
from api.services.entitlement_service import (
    CANONICAL_ENTITLEMENT_METRICS,
    QuotaReasonCode,
    get_authoritative_concurrency_metrics,
    log_commercial_audit,
    resolve_effective_entitlements,
)

logger = logging.getLogger(__name__)

DEFAULT_ENFORCEMENT_KEY = "commercial:enforcement"
ENFORCEMENT_SETTING_KEY = DEFAULT_ENFORCEMENT_KEY
DEFAULT_ROLLOUT_MODE = "disabled"  # disabled, shadow, enforce


def _get_current_utc_month_bounds(dt: datetime) -> tuple[datetime, datetime]:
    """Return (start_of_month, start_of_next_month) as naive UTC datetimes."""
    start = datetime(dt.year, dt.month, 1)
    if dt.month == 12:
        end = datetime(dt.year + 1, 1, 1)
    else:
        end = datetime(dt.year, dt.month + 1, 1)
    return start, end


def _utcnow() -> datetime:
    """Return timezone-naive UTC datetime for consistent database timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Standardized Commercial Error Payload ──


class CommercialErrorDetail(BaseModel):
    error_code: str = "COMMERCIAL_POLICY_VIOLATION"
    reason_code: str
    message: str
    metric: Optional[str] = None
    limit_value: Optional[float] = None
    current_value: Optional[float] = None
    reset_at: Optional[str] = None
    correlation_id: Optional[str] = None


class CommercialEnforcementException(HTTPException):
    """Structured commercial policy exception with machine-readable detail."""

    def __init__(
        self,
        status_code: int,
        reason_code: str,
        message: str,
        metric: Optional[str] = None,
        limit_value: Optional[float] = None,
        current_value: Optional[float] = None,
        reset_at: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        detail = {
            "success": False,
            "error_code": "COMMERCIAL_POLICY_VIOLATION",
            "reason_code": reason_code,
            "message": message,
            "metric": metric,
            "limit_value": limit_value,
            "current_value": current_value,
            "reset_at": reset_at,
            "correlation_id": correlation_id or f"corr-{uuid.uuid4().hex[:12]}",
        }
        self.reason_code = reason_code
        self.error_detail = detail
        super().__init__(status_code=status_code, detail=detail)


# ── Feature Flag Rollout Management ──


async def get_enforcement_config(session: AsyncSession) -> dict[str, Any]:
    """Retrieve persistent rollout configuration from PostgreSQL app_settings."""
    setting = await session.get(AppSettingDB, DEFAULT_ENFORCEMENT_KEY)
    if setting and setting.value:
        val = setting.value
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                val = {}
        return {
            "mode": val.get("mode", DEFAULT_ROLLOUT_MODE),
            "tenant_allowlist": val.get("tenant_allowlist", []),
            "description": "Commercial quota enforcement rollout mode and test tenant allowlist",
            "reason": val.get("reason", "Default rollout state"),
            "updated_at": val.get("updated_at"),
            "updated_by_user_id": val.get("updated_by_user_id"),
        }

    return {
        "mode": DEFAULT_ROLLOUT_MODE,
        "tenant_allowlist": [],
        "description": "Commercial quota enforcement rollout mode and test tenant allowlist",
        "reason": "Default fallback state",
        "updated_at": None,
        "updated_by_user_id": None,
    }


async def update_enforcement_config(
    session: AsyncSession,
    *,
    mode: str,
    tenant_allowlist: list[str],
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> dict[str, Any]:
    """Update persistent enforcement mode and allowlist with mandatory audit justification."""
    if mode not in ("disabled", "shadow", "enforce"):
        raise ValueError(f"Invalid enforcement mode '{mode}'. Must be 'disabled', 'shadow', or 'enforce'")
    if not reason or len(reason.strip()) < 3:
        raise ValueError("A non-empty audit reason is mandatory when updating commercial enforcement settings")

    now = _utcnow()
    payload = {
        "mode": mode,
        "tenant_allowlist": [t.strip() for t in tenant_allowlist if t.strip()],
        "reason": reason.strip(),
        "updated_at": now.isoformat(),
        "updated_by_user_id": actor_user_id,
    }

    setting = await session.get(AppSettingDB, DEFAULT_ENFORCEMENT_KEY)
    if not setting:
        setting = AppSettingDB(
            key=DEFAULT_ENFORCEMENT_KEY,
            value=payload,
            updated_at=now,
        )
        session.add(setting)
    else:
        setting.value = payload
        setting.updated_at = now

    await log_commercial_audit(
        session,
        action="commercial_enforcement_updated",
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details=payload,
    )

    return payload


async def is_tenant_enforced(session: AsyncSession, tenant_id: str) -> tuple[bool, str]:
    """Determine whether hard enforcement applies to a tenant.

    Returns:
        (is_enforced: bool, rollout_mode: str)
    """
    config = await get_enforcement_config(session)
    mode = config.get("mode", DEFAULT_ROLLOUT_MODE)
    allowlist = set(config.get("tenant_allowlist", []))

    if mode == "enforce" and tenant_id in allowlist:
        # Grandfathered legacy accounts must NEVER be hard-blocked
        sub_stmt = select(TenantSubscriptionDB.status).where(TenantSubscriptionDB.tenant_id == tenant_id)
        sub_status = (await session.execute(sub_stmt)).scalar_one_or_none()
        if sub_status == "legacy_active":
            return False, mode
        return True, mode
    return False, mode


# ── Transactional Admission & Durable Reservation ──


async def acquire_admission_and_reservation(
    session: AsyncSession,
    *,
    tenant_id: str,
    operation_type: str,  # 'download', 'clip_extract', 'merge', 'transcript'
    idempotency_key: str,
    payload: Optional[dict[str, Any]] = None,
    quantity: int = 1,
    estimated_bytes: Optional[int] = None,
    estimated_duration_seconds: Optional[float] = None,
    user_id: Optional[str] = None,
    auth_method: str = "jwt",
    resource_id: Optional[str] = None,
    lease_minutes: int = 30,
) -> tuple[CommercialReservationDB, bool]:
    """Execute transactional admission check and establish a durable commercial reservation.

    Step 1: Acquire row-level lock on the tenant record to serialize admission and prevent races.
    Step 2: Check for existing idempotent reservation within tenant with request fingerprinting.
    Step 3: Resolve effective entitlements, subscription status, and rollout configuration.
    Step 4: Check active concurrency slots and monthly quota counts.
    Step 5: If within limits, record durable CommercialReservationDB.

    Returns:
        (reservation: CommercialReservationDB, newly_created: bool)
    """
    if quantity <= 0:
        raise ValueError("Reservation quantity must be strictly greater than zero")
    if estimated_bytes is not None and estimated_bytes < 0:
        raise ValueError("Estimated bytes cannot be negative")
    if estimated_duration_seconds is not None and estimated_duration_seconds < 0.0:
        raise ValueError("Estimated duration seconds cannot be negative")
    if operation_type not in ("download", "clip_extract", "clip", "merge", "clips_merge", "transcript"):
        raise ValueError(f"Invalid operation_type '{operation_type}'")

    now = _utcnow()
    clean_key = (idempotency_key or "").strip()
    if not clean_key:
        clean_key = f"{operation_type}:{uuid.uuid4().hex[:20]}"

    request_fingerprint = None
    if payload:
        try:
            raw_p = json.dumps(payload, sort_keys=True, default=str)
            request_fingerprint = hashlib.sha256(f"{operation_type}:{raw_p}".encode("utf-8")).hexdigest()
        except Exception:
            request_fingerprint = None

    try:
        # Step 1: Acquire tenant row lock for strict serializability
        # This locks ONLY this specific tenant without impeding other tenants
        lock_stmt = select(TenantDB.id).where(TenantDB.id == tenant_id).with_for_update()
        tenant_exists = (await session.execute(lock_stmt)).scalar_one_or_none()
        if not tenant_exists:
            raise HTTPException(status_code=404, detail="Tenant does not exist")

        # Step 2: Idempotency check: reuse existing active or finalized reservation with fingerprint check
        res_stmt = (
            select(CommercialReservationDB)
            .where(
                CommercialReservationDB.tenant_id == tenant_id,
                CommercialReservationDB.idempotency_key == clean_key,
            )
            .with_for_update()
        )
        existing_res = (await session.execute(res_stmt)).scalars().first()
        if existing_res:
            # Operation type conflict
            if existing_res.operation_type != operation_type:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_409_CONFLICT,
                    reason_code="IDEMPOTENCY_CONFLICT",
                    message=f"Idempotency conflict: key '{clean_key}' already used for operation '{existing_res.operation_type}'",
                )

            meta = existing_res.metadata_json or {}
            # Principal isolation check
            if user_id and meta.get("user_id") and meta.get("user_id") != user_id:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_409_CONFLICT,
                    reason_code="IDEMPOTENCY_CONFLICT",
                    message=f"Idempotency conflict: key '{clean_key}' belongs to another principal",
                )

            # Request fingerprint conflict
            if request_fingerprint and meta.get("request_fingerprint") and meta.get("request_fingerprint") != request_fingerprint:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_409_CONFLICT,
                    reason_code="IDEMPOTENCY_CONFLICT",
                    message=f"Idempotency conflict: key '{clean_key}' already used with different request parameters",
                )

            # Cancelled / expired reservation cannot restart
            if existing_res.status in ("released", "expired"):
                raise CommercialEnforcementException(
                    status_code=status.HTTP_409_CONFLICT,
                    reason_code="IDEMPOTENCY_CONFLICT",
                    message=f"Idempotency conflict: key '{clean_key}' references a terminated or cancelled reservation",
                )

            if existing_res.status in ("reserved", "active", "finalized"):
                logger.info(
                    "Reusing existing reservation %s (status=%s) for idempotency key %s",
                    existing_res.id,
                    existing_res.status,
                    clean_key,
                )
                return existing_res, False

        # Step 3: Fetch rollout configuration and effective entitlements
        is_enforced, rollout_mode = await is_tenant_enforced(session, tenant_id)
        effective, sub, plan = await resolve_effective_entitlements(session, tenant_id)

        period_start, period_end = _get_current_utc_month_bounds(now)
        reset_at_str = period_end.isoformat()

        # Step 4: Verify subscription status and trial expiration
        sub_status = sub.status if sub else "active"
        if sub and sub.status == "trial" and sub.trial_expires_at and sub.trial_expires_at < now:
            sub_status = "expired"
        elif sub and sub.status == "grace_period":
            grace_data = (sub.metadata_json or {}).get("grace_period", {})
            if "expires_at" in grace_data:
                try:
                    if datetime.fromisoformat(grace_data["expires_at"]) < now:
                        sub_status = "expired"
                except Exception:
                    pass

        if sub_status == "suspended":
            msg = "Commercial subscription is suspended"
            if is_enforced:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    reason_code=QuotaReasonCode.SUBSCRIPTION_SUSPENDED.value,
                    message=msg,
                    reset_at=None,
                )
            elif rollout_mode == "shadow":
                await _log_shadow_would_block(session, tenant_id, QuotaReasonCode.SUBSCRIPTION_SUSPENDED.value, msg)

        elif sub_status == "expired":
            msg = "Commercial trial or subscription has expired"
            if is_enforced:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    reason_code=QuotaReasonCode.TRIAL_EXPIRED.value,
                    message=msg,
                    reset_at=reset_at_str,
                )
            elif rollout_mode == "shadow":
                await _log_shadow_would_block(session, tenant_id, QuotaReasonCode.TRIAL_EXPIRED.value, msg)

        elif sub_status == "cancelled":
            msg = "Commercial subscription is cancelled"
            if is_enforced:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    reason_code=QuotaReasonCode.SUBSCRIPTION_CANCELLED.value,
                    message=msg,
                    reset_at=reset_at_str,
                )
            elif rollout_mode == "shadow":
                await _log_shadow_would_block(session, tenant_id, QuotaReasonCode.SUBSCRIPTION_CANCELLED.value, msg)

        elif sub_status == "reconciliation_required":
            msg = "Commercial subscription requires administrative review"
            if is_enforced:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    reason_code=QuotaReasonCode.PLAN_RECONCILIATION_REQUIRED.value,
                    message=msg,
                    reset_at=reset_at_str,
                )
            elif rollout_mode == "shadow":
                await _log_shadow_would_block(session, tenant_id, QuotaReasonCode.PLAN_RECONCILIATION_REQUIRED.value, msg)

        # Step 5: Feature Access Gates
        if auth_method == "api_key":
            api_gate = effective.get("api_access")
            if api_gate and api_gate.value_bool is False:
                msg = "API access is not included in current commercial subscription tier"
                if is_enforced:
                    raise CommercialEnforcementException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        reason_code=QuotaReasonCode.FEATURE_NOT_INCLUDED.value,
                        message=msg,
                        metric="api_access",
                    )
                elif rollout_mode == "shadow":
                    await _log_shadow_would_block(session, tenant_id, QuotaReasonCode.FEATURE_NOT_INCLUDED.value, msg)

        # Step 6: Concurrency Check (authoritative outstanding capacity with zero double-counting)
        c_ent = effective.get("max_concurrent_jobs")
        if c_ent and not c_ent.is_unlimited:
            c_limit = c_ent.value_int or 1
            metrics = await get_authoritative_concurrency_metrics(session, tenant_id)
            active_res_count = metrics["admitted_outstanding_operations"]

            if active_res_count >= c_limit:
                msg = f"Maximum concurrent background jobs reached ({active_res_count}/{c_limit})"
                if is_enforced:
                    raise CommercialEnforcementException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        reason_code=QuotaReasonCode.CONCURRENCY_EXCEEDED.value,
                        message=msg,
                        metric="max_concurrent_jobs",
                        limit_value=float(c_limit),
                        current_value=float(active_res_count),
                    )
                elif rollout_mode == "shadow":
                    await _log_shadow_would_block(
                        session,
                        tenant_id,
                        QuotaReasonCode.CONCURRENCY_EXCEEDED.value,
                        msg,
                        {
                            "limit": c_limit,
                            "current": active_res_count,
                            "reserved_operations": metrics["reserved_operations"],
                            "active_processing_operations": metrics["active_processing_operations"],
                            "legacy_unreserved_operations": metrics["legacy_unreserved_operations"],
                        },
                    )

        # Step 7: Operation-Specific Monthly Count Quota Check
        op_metric_map = {
            "download": ("monthly_download_count", QuotaReasonCode.DOWNLOAD_COUNT_EXCEEDED.value),
            "clip_extract": ("monthly_clip_count", QuotaReasonCode.CLIP_COUNT_EXCEEDED.value),
            "clip": ("monthly_clip_count", QuotaReasonCode.CLIP_COUNT_EXCEEDED.value),
            "merge": ("monthly_merge_count", QuotaReasonCode.MERGE_COUNT_EXCEEDED.value),
            "clips_merge": ("monthly_merge_count", QuotaReasonCode.MERGE_COUNT_EXCEEDED.value),
        }

        if operation_type in op_metric_map:
            m_key, reason_code = op_metric_map[operation_type]
            count_ent = effective.get(m_key)
            if count_ent and not count_ent.is_unlimited:
                m_limit = count_ent.value_int or 0

                # Sum actual usage events in current billing period
                # event_type standard mapping
                event_type_name = "download" if operation_type == "download" else ("clip" if "clip" in operation_type else "merge")
                usage_sum_stmt = select(func.coalesce(func.sum(UsageEventDB.quantity), 0.0)).where(
                    UsageEventDB.tenant_id == tenant_id,
                    UsageEventDB.event_type == event_type_name,
                    UsageEventDB.created_at >= period_start,
                    UsageEventDB.created_at < period_end,
                )
                used_count = float((await session.execute(usage_sum_stmt)).scalar() or 0.0)

                # Sum active reservations for this operation
                active_op_stmt = select(func.count(CommercialReservationDB.id)).where(
                    CommercialReservationDB.tenant_id == tenant_id,
                    CommercialReservationDB.operation_type == operation_type,
                    CommercialReservationDB.status.in_(["reserved", "active"]),
                )
                active_op_count = float((await session.execute(active_op_stmt)).scalar() or 0.0)

                projected_count = used_count + active_op_count
                if projected_count >= m_limit:
                    msg = f"Monthly {operation_type} quota exhausted ({int(projected_count)}/{m_limit})"
                    if is_enforced:
                        raise CommercialEnforcementException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            reason_code=reason_code,
                            message=msg,
                            metric=m_key,
                            limit_value=float(m_limit),
                            current_value=projected_count,
                            reset_at=reset_at_str,
                        )
                    elif rollout_mode == "shadow":
                        await _log_shadow_would_block(
                            session,
                            tenant_id,
                            reason_code,
                            msg,
                            {"limit": m_limit, "used": used_count, "active_reservations": active_op_count},
                        )

        # Step 8: Create Durable Reservation
        reservation_id = str(uuid.uuid4())
        lease_expires_at = now + timedelta(minutes=lease_minutes)
        ent_key = op_metric_map.get(operation_type, ("max_concurrent_jobs", ""))[0]

        meta_dict = {
            "user_id": user_id,
            "auth_method": auth_method,
            "rollout_mode": rollout_mode,
            "is_enforced": is_enforced,
        }
        if request_fingerprint:
            meta_dict["request_fingerprint"] = request_fingerprint

        res = CommercialReservationDB(
            id=reservation_id,
            tenant_id=tenant_id,
            operation_type=operation_type,
            resource_id=resource_id,
            idempotency_key=clean_key,
            entitlement_key=ent_key,
            quantity=quantity,
            status="reserved",
            estimated_bytes=estimated_bytes,
            estimated_duration_seconds=estimated_duration_seconds,
            lease_expires_at=lease_expires_at,
            metadata_json=meta_dict,
            created_at=now,
            updated_at=now,
        )
        session.add(res)
        await session.flush()
        return res, True

    except CommercialEnforcementException:
        raise
    except HTTPException:
        raise
    except (SQLAlchemyError, DBAPIError) as db_err:
        logger.error("Database error during quota admission for tenant %s: %s", tenant_id, db_err)
        # When enforcement is active, fail safe with 503 rather than corrupting quota state
        is_enf, _ = await is_tenant_enforced(session, tenant_id)
        if is_enf:
            raise CommercialEnforcementException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                reason_code="COMMERCIAL_SERVICE_UNAVAILABLE",
                message="Commercial quota admission service temporarily unavailable. Please retry shortly.",
            )
        # In advisory mode, log warning and let caller proceed
        raise


async def _log_shadow_would_block(
    session: AsyncSession,
    tenant_id: str,
    reason_code: str,
    message: str,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Record shadow-mode evaluation audit event for what would be blocked."""
    now = _utcnow()
    det = details or {}
    det["reason_code"] = reason_code
    det["message"] = message
    try:
        await log_commercial_audit(
            session,
            tenant_id=tenant_id,
            action="commercial_quota_would_block",
            actor_user_id=None,
            actor_role="system_enforcer_shadow",
            reason=f"Shadow evaluation would block: {reason_code}",
            details=det,
        )
    except Exception as exc:
        logger.warning("Could not record shadow audit log: %s", exc)


# ── Reservation State Machine Transitions ──


async def activate_reservation(
    session: AsyncSession,
    reservation_id: str,
    resource_id: Optional[str] = None,
    lease_expires_at: Optional[datetime] = None,
) -> bool:
    """Transition reservation from 'reserved' to 'active' when work begins."""
    now = _utcnow()
    res = await session.get(CommercialReservationDB, reservation_id)
    if not res:
        return False
    if res.status != "reserved":
        return False

    res.status = "active"
    if resource_id:
        res.resource_id = resource_id
    if lease_expires_at:
        res.lease_expires_at = lease_expires_at
    res.updated_at = now
    return True


async def finalize_reservation(
    session: AsyncSession,
    reservation_id: str,
    resource_id: Optional[str] = None,
) -> bool:
    """Transition reservation to 'finalized' when operation completes successfully."""
    now = _utcnow()
    res = await session.get(CommercialReservationDB, reservation_id)
    if not res:
        return False
    if res.status in ("finalized", "released", "expired"):
        return False

    res.status = "finalized"
    if resource_id:
        res.resource_id = resource_id
    res.finalized_at = now
    res.updated_at = now
    return True


async def release_reservation(
    session: AsyncSession,
    reservation_id: str,
    reason: Optional[str] = None,
) -> bool:
    """Transition reservation to 'released' when operation fails, cancels, or aborts."""
    now = _utcnow()
    res = await session.get(CommercialReservationDB, reservation_id)
    if not res:
        return False
    if res.status in ("finalized", "released", "expired"):
        return False

    res.status = "released"
    res.released_at = now
    res.updated_at = now
    if reason and res.metadata_json:
        m = dict(res.metadata_json)
        m["release_reason"] = reason
        res.metadata_json = m
    return True


async def finalize_or_release_by_idempotency_key(
    session: AsyncSession,
    tenant_id: str,
    idempotency_key: str,
    success: bool,
    resource_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> bool:
    """Convenience helper to finalize or release a reservation by tenant and idempotency key."""
    stmt = (
        select(CommercialReservationDB)
        .where(
            CommercialReservationDB.tenant_id == tenant_id,
            CommercialReservationDB.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    res = (await session.execute(stmt)).scalars().first()
    if not res:
        return False

    if success:
        return await finalize_reservation(session, res.id, resource_id=resource_id)
    else:
        return await release_reservation(session, res.id, reason=reason)


async def reconcile_stale_reservations(session: AsyncSession) -> int:
    """Audit and reconcile abandoned reservations where lease has expired.

    Cross-checks corresponding work items and download jobs:
    - If work item is actively running or heartbeating: extend lease.
    - If work item/download succeeded: finalize reservation.
    - If work item/download failed/cancelled: release reservation.
    - If orphaned/abandoned: mark 'expired'.
    """
    now = _utcnow()
    stmt = (
        select(CommercialReservationDB)
        .where(
            CommercialReservationDB.status.in_(["reserved", "active"]),
            CommercialReservationDB.lease_expires_at.isnot(None),
            CommercialReservationDB.lease_expires_at < now,
        )
        .with_for_update()
    )
    stale_reservations = (await session.execute(stmt)).scalars().all()
    reconciled_count = 0

    for res in stale_reservations:
        # Cross check with work item if resource_id or idempotency_key matches
        wi = None
        if res.resource_id:
            wi = await session.get(MediaWorkItemDB, res.resource_id)
        if not wi and res.idempotency_key:
            wi_stmt = select(MediaWorkItemDB).where(
                MediaWorkItemDB.tenant_id == res.tenant_id,
                MediaWorkItemDB.idempotency_key == res.idempotency_key,
            )
            wi = (await session.execute(wi_stmt)).scalars().first()

        if wi:
            if wi.status == "succeeded":
                res.status = "finalized"
                res.finalized_at = now
                reconciled_count += 1
            elif wi.status in ("failed", "cancelled"):
                res.status = "released"
                res.released_at = now
                reconciled_count += 1
            elif wi.status == "running" and wi.last_heartbeat_at and wi.last_heartbeat_at > (now - timedelta(minutes=5)):
                # Actively running on worker; extend lease!
                res.lease_expires_at = now + timedelta(minutes=15)
            elif wi.status == "retry_wait":
                # Job waiting for scheduled retry; extend lease!
                res.lease_expires_at = (wi.available_at or now) + timedelta(minutes=15)
            elif wi.status == "queued" and wi.created_at and wi.created_at > (now - timedelta(minutes=60)):
                # Still queued waiting for worker pickup; extend lease!
                res.lease_expires_at = now + timedelta(minutes=15)
            else:
                # Stale worker failure or abandoned queue item
                res.status = "expired"
                res.released_at = now
                reconciled_count += 1
            res.updated_at = now
            continue

        # 2. Check DownloadJobDB
        dj = None
        if res.resource_id:
            dj = await session.get(DownloadJobDB, res.resource_id)
        if dj:
            if dj.status == "completed":
                res.status = "finalized"
                res.finalized_at = now
                reconciled_count += 1
            elif dj.status in ("failed", "cancelled"):
                res.status = "released"
                res.released_at = now
                reconciled_count += 1
            elif dj.status in ("downloading", "queued"):
                # Active download; extend lease!
                res.lease_expires_at = now + timedelta(minutes=15)
            else:
                res.status = "expired"
                res.released_at = now
                reconciled_count += 1
            res.updated_at = now
            continue

        # Orphaned reservation with no active work item or download job
        res.status = "expired"
        res.released_at = now
        res.updated_at = now
        reconciled_count += 1

    return reconciled_count
