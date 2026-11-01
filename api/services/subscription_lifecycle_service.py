"""Subscription lifecycle state machine, transitions, and commercial administration (STEP 04-A3)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from api.services.entitlement_service import (
    CANONICAL_ENTITLEMENT_METRICS,
    evaluate_tenant_quotas,
    log_commercial_audit,
    resolve_effective_entitlements,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Standardized UTC timestamp without timezone offset for database consistency."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Valid lifecycle states (TASK 3)
VALID_SUBSCRIPTION_STATES = {
    "pending_activation",
    "trial",
    "active",
    "grace_period",
    "scheduled_cancellation",
    "cancelled",
    "expired",
    "suspended",
    "legacy_active",
    "reconciliation_required",
}

# State machine allowed transitions map
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending_activation": {"trial", "active", "cancelled"},
    "trial": {"active", "grace_period", "expired", "cancelled", "suspended"},
    "active": {"active", "grace_period", "scheduled_cancellation", "cancelled", "expired", "suspended"},
    "grace_period": {"active", "expired", "cancelled", "suspended"},
    "scheduled_cancellation": {"active", "cancelled", "suspended"},
    "cancelled": {"active", "trial"},
    "expired": {"active", "trial", "grace_period"},
    "suspended": {"active", "trial", "cancelled", "legacy_active", "expired", "grace_period"},
    "legacy_active": {"legacy_active", "active", "suspended"},
    "reconciliation_required": {"legacy_active", "active", "suspended"},
}


def validate_state_transition(current_status: str, target_status: str) -> None:
    """Validate that transition between subscription statuses is allowed by state machine."""
    if target_status not in VALID_SUBSCRIPTION_STATES:
        raise ValueError(
            f"Invalid target subscription status '{target_status}'. Must be one of {sorted(VALID_SUBSCRIPTION_STATES)}"
        )

    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise ValueError(
            f"Invalid subscription transition from '{current_status}' to '{target_status}'. "
            f"Allowed next states: {sorted(allowed) if allowed else 'none'}"
        )


def calculate_next_period_end(start: datetime, interval: str = "monthly") -> Optional[datetime]:
    """Calculate next period boundary from a starting UTC timestamp."""
    if interval == "lifetime":
        return None
    elif interval == "annual":
        try:
            return start.replace(year=start.year + 1)
        except ValueError:
            return start + timedelta(days=365)
    else:  # monthly default
        month = start.month + 1
        year = start.year
        if month > 12:
            month = 1
            year += 1
        day = min(start.day, 28)
        return datetime(year, month, day, start.hour, start.minute, start.second)


# ── Immediate Plan Assignment & Upgrades (TASK 4 & 5) ──


async def assign_tenant_plan_lifecycle(
    session: AsyncSession,
    *,
    tenant_id: str,
    plan_id: str,
    status: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
    reason: str,
) -> TenantSubscriptionDB:
    """Assign an approved published plan immediately to a tenant with audit trail."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for plan assignment")

    plan = await session.get(CommercialPlanDB, plan_id)
    if not plan:
        raise ValueError(f"Plan ID '{plan_id}' does not exist")
    if plan.status == "retired":
        raise ValueError(f"Plan '{plan.plan_code}' (v{plan.version}) is retired and cannot accept new assignments")
    if plan.status == "draft":
        raise ValueError(f"Plan '{plan.plan_code}' (v{plan.version}) is in draft status and cannot be assigned until published")

    now = _utcnow()
    stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(stmt)).scalars().first()

    target_status = status or "active"
    old_plan_id = sub.plan_id if sub else None
    old_status = sub.status if sub else "unassigned"

    if sub:
        if old_status != target_status:
            validate_state_transition(old_status, target_status)

        sub.plan_id = plan_id
        sub.status = target_status
        sub.current_period_start = now
        sub.current_period_end = calculate_next_period_end(now, plan.billing_interval)
        sub.updated_at = now

        meta = dict(sub.metadata_json or {})
        meta["payment_verification_state"] = "none" if plan.tier_type == "free" else "manual_grant"
        meta["service_access_state"] = "active"
        sub.metadata_json = meta
    else:
        meta = {
            "payment_verification_state": "none" if plan.tier_type == "free" else "manual_grant",
            "service_access_state": "active",
        }
        sub = TenantSubscriptionDB(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            plan_id=plan_id,
            status=target_status,
            current_period_start=now,
            current_period_end=calculate_next_period_end(now, plan.billing_interval),
            metadata_json=meta,
            created_at=now,
            updated_at=now,
        )
        session.add(sub)

    await log_commercial_audit(
        session,
        action="tenant_plan_assigned",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={
            "old_plan_id": old_plan_id,
            "new_plan_id": plan_id,
            "old_status": old_status,
            "new_status": target_status,
            "plan_code": plan.plan_code,
            "plan_version": plan.version,
        },
    )

    await session.flush()
    return sub


# ── Scheduled Plan Transitions (TASK 5 & 6) ──


async def schedule_plan_transition(
    session: AsyncSession,
    *,
    tenant_id: str,
    target_plan_id: Optional[str],
    transition_type: str,
    scheduled_at: Optional[datetime] = None,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
    reason: str,
) -> ScheduledSubscriptionTransitionDB:
    """Schedule a future plan upgrade, downgrade, or cancellation."""
    if transition_type not in ("upgrade", "downgrade", "cancel", "renewal"):
        raise ValueError(f"Invalid transition type '{transition_type}'. Must be upgrade, downgrade, cancel, or renewal")
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for scheduling subscription transitions")

    # Fetch tenant subscription
    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()
    if not sub:
        raise ValueError(f"Tenant '{tenant_id}' has no existing subscription to schedule transitions for")

    # Validate target plan if not cancel
    if transition_type != "cancel":
        if not target_plan_id:
            raise ValueError(f"target_plan_id is required for '{transition_type}' transition")
        target_plan = await session.get(CommercialPlanDB, target_plan_id)
        if not target_plan:
            raise ValueError(f"Target plan ID '{target_plan_id}' does not exist")
        if target_plan.status == "draft":
            raise ValueError(f"Target plan '{target_plan.plan_code}' is draft and cannot be scheduled")
        if target_plan.status == "retired":
            raise ValueError(f"Target plan '{target_plan.plan_code}' is retired and cannot be scheduled")

    # Check for existing pending transition to prevent contradictory schedules (TASK 6)
    pending_stmt = select(ScheduledSubscriptionTransitionDB).where(
        ScheduledSubscriptionTransitionDB.tenant_id == tenant_id,
        ScheduledSubscriptionTransitionDB.status == "pending",
    )
    existing_pending = (await session.execute(pending_stmt)).scalars().first()
    if existing_pending:
        raise ValueError(
            f"A pending scheduled transition (ID '{existing_pending.id}', type '{existing_pending.transition_type}') "
            f"already exists for tenant '{tenant_id}'. Cancel it before scheduling a new transition."
        )

    now = _utcnow()
    # Resolve scheduled_at
    if scheduled_at is None:
        if sub.current_period_end and sub.current_period_end > now:
            scheduled_at = sub.current_period_end
        else:
            scheduled_at = now + timedelta(days=30)

    if scheduled_at <= now:
        raise ValueError("Scheduled transition timestamp must be in the future (UTC)")

    record = ScheduledSubscriptionTransitionDB(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        target_plan_id=target_plan_id,
        transition_type=transition_type,
        status="pending",
        scheduled_at=scheduled_at,
        reason=reason.strip(),
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        metadata_json={
            "prior_plan_id": sub.plan_id,
            "prior_status": sub.status,
        },
        created_at=now,
        updated_at=now,
    )
    session.add(record)

    # If scheduling cancellation, mark subscription as scheduled_cancellation
    if transition_type == "cancel":
        validate_state_transition(sub.status, "scheduled_cancellation")
        sub.status = "scheduled_cancellation"
        sub.updated_at = now

    await log_commercial_audit(
        session,
        action="scheduled_transition_created",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={
            "transition_id": record.id,
            "transition_type": transition_type,
            "target_plan_id": target_plan_id,
            "scheduled_at": scheduled_at.isoformat(),
        },
    )

    await session.flush()
    return record


async def cancel_scheduled_transition(
    session: AsyncSession,
    *,
    tenant_id: str,
    transition_id: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
    reason: str,
) -> ScheduledSubscriptionTransitionDB:
    """Cancel a pending scheduled plan transition."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for cancelling a scheduled transition")

    rec = await session.get(ScheduledSubscriptionTransitionDB, transition_id)
    if not rec or rec.tenant_id != tenant_id:
        raise ValueError(f"Scheduled transition '{transition_id}' not found for tenant '{tenant_id}'")
    if rec.status != "pending":
        raise ValueError(f"Cannot cancel scheduled transition with status '{rec.status}'. Only pending transitions can be cancelled.")

    now = _utcnow()
    rec.status = "cancelled"
    rec.updated_at = now

    # If subscription was in scheduled_cancellation and this was a cancellation transition, restore to active
    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()
    if sub and sub.status == "scheduled_cancellation" and rec.transition_type == "cancel":
        sub.status = "active"
        sub.updated_at = now

    await log_commercial_audit(
        session,
        action="scheduled_transition_cancelled",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={
            "transition_id": rec.id,
            "transition_type": rec.transition_type,
            "target_plan_id": rec.target_plan_id,
        },
    )

    await session.flush()
    return rec


# ── Scheduled Transition Processing & Scheduler Observability (STEP 04-A3 & STEP 04-B1) ──

SCHEDULER_CONFIG_KEY = "commercial:scheduler_config"
SCHEDULER_STATE_KEY = "commercial:scheduler_state"

DEFAULT_SCHEDULER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "paused": False,
    "rollout_mode": "allowlist_only",
    "tenant_allowlist": [],
    "poll_interval_seconds": 60,
    "description": "Commercial scheduled transition background processor configuration",
}


async def get_scheduler_config(session: AsyncSession) -> dict[str, Any]:
    """Retrieve the persistent configuration for the scheduled transition processor."""
    stmt = select(AppSettingDB).where(AppSettingDB.key == SCHEDULER_CONFIG_KEY)
    rec = (await session.execute(stmt)).scalars().first()
    if rec and isinstance(rec.value, dict):
        cfg = dict(DEFAULT_SCHEDULER_CONFIG)
        cfg.update(rec.value)
        cfg["updated_at"] = rec.updated_at.isoformat() if rec.updated_at else None
        return cfg
    return dict(DEFAULT_SCHEDULER_CONFIG)


async def update_scheduler_config(
    session: AsyncSession,
    *,
    paused: Optional[bool] = None,
    rollout_mode: Optional[str] = None,
    tenant_allowlist: Optional[list[str]] = None,
    poll_interval_seconds: Optional[int] = None,
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> dict[str, Any]:
    """Update scheduled transition processor configuration with audit tracking."""
    if not reason or not reason.strip():
        raise ValueError("Administrative justification reason is mandatory")

    stmt = select(AppSettingDB).where(AppSettingDB.key == SCHEDULER_CONFIG_KEY)
    rec = (await session.execute(stmt)).scalars().first()

    current_cfg = dict(DEFAULT_SCHEDULER_CONFIG)
    if rec and isinstance(rec.value, dict):
        current_cfg.update(rec.value)

    if paused is not None:
        current_cfg["paused"] = paused
    if rollout_mode is not None:
        if rollout_mode not in ("test_tenants_only", "allowlist_only", "all_tenants"):
            raise ValueError("rollout_mode must be test_tenants_only, allowlist_only, or all_tenants")
        current_cfg["rollout_mode"] = rollout_mode
    if tenant_allowlist is not None:
        current_cfg["tenant_allowlist"] = [t.strip() for t in tenant_allowlist if t.strip()]
    if poll_interval_seconds is not None:
        if poll_interval_seconds < 1 or poll_interval_seconds > 3600:
            raise ValueError("poll_interval_seconds must be between 1 and 3600")
        current_cfg["poll_interval_seconds"] = poll_interval_seconds

    now = _utcnow()
    current_cfg["updated_by_user_id"] = actor_user_id
    current_cfg["reason"] = reason.strip()

    if not rec:
        rec = AppSettingDB(
            key=SCHEDULER_CONFIG_KEY,
            value=current_cfg,
            updated_at=now,
        )
        session.add(rec)
    else:
        rec.value = current_cfg
        rec.updated_at = now

    await log_commercial_audit(
        session,
        action="scheduler_config_updated",
        tenant_id=None,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details=current_cfg,
    )
    await session.flush()
    current_cfg["updated_at"] = now.isoformat()
    return current_cfg


async def get_scheduler_state(session: AsyncSession) -> dict[str, Any]:
    """Retrieve the latest runtime observability state of the background scheduler."""
    stmt = select(AppSettingDB).where(AppSettingDB.key == SCHEDULER_STATE_KEY)
    rec = (await session.execute(stmt)).scalars().first()
    if rec and isinstance(rec.value, dict):
        data = dict(rec.value)
        data["updated_at"] = rec.updated_at.isoformat() if rec.updated_at else None
        return data
    return {
        "last_scan_at": None,
        "last_executed_count": 0,
        "worker_id": None,
        "status": "idle",
        "updated_at": None,
    }


async def update_scheduler_state(
    session: AsyncSession,
    state_payload: Optional[dict[str, Any]] = None,
    *,
    worker_id: Optional[str] = None,
    status: Optional[str] = None,
    executed_count: Optional[int] = None,
    scan_succeeded: bool = True,
    error_message: Optional[str] = None,
) -> dict[str, Any]:
    """Record runtime state and worker heartbeats of the background scheduler cluster."""
    stmt = select(AppSettingDB).where(AppSettingDB.key == SCHEDULER_STATE_KEY)
    rec = (await session.execute(stmt)).scalars().first()
    now = _utcnow()
    existing: dict[str, Any] = dict(rec.value) if rec and isinstance(rec.value, dict) else {}

    # Extract parameters either from payload dict or kwargs
    wid = worker_id or (state_payload.get("worker_id") if state_payload else None) or "worker_main"
    st = status or (state_payload.get("status") if state_payload else "idle")
    cnt = executed_count if executed_count is not None else ((state_payload.get("last_executed_count") if state_payload else 0) or 0)

    workers = dict(existing.get("active_workers") or {})
    workers[wid] = {
        "last_heartbeat": now.isoformat(),
        "status": st,
        "last_executed_count": cnt,
    }

    # Prune workers that have not heartbeated in 5 minutes (300s)
    cutoff = now - timedelta(seconds=300)
    pruned_workers = {}
    for w_key, w_val in workers.items():
        try:
            if isinstance(w_val, dict) and "last_heartbeat" in w_val:
                hb = datetime.fromisoformat(w_val["last_heartbeat"])
                if hb >= cutoff:
                    pruned_workers[w_key] = w_val
        except Exception:
            pass

    cluster_status = "paused" if st == "paused" else ("scanning" if st == "scanning" else ("active" if cnt > 0 else "idle"))

    merged_payload = {
        **existing,
        **(state_payload or {}),
        "cluster_status": cluster_status,
        "last_scan_at": now.isoformat(),
        "last_executed_count": cnt,
        "worker_id": wid,
        "status": st,
        "active_workers": pruned_workers,
        "active_worker_count": len(pruned_workers),
        "last_successful_scan_at": now.isoformat() if scan_succeeded else existing.get("last_successful_scan_at"),
        "last_failed_scan_at": now.isoformat() if not scan_succeeded else existing.get("last_failed_scan_at"),
        "last_error": error_message if not scan_succeeded else None,
        "updated_at": now.isoformat(),
    }

    if not rec:
        rec = AppSettingDB(
            key=SCHEDULER_STATE_KEY,
            value=merged_payload,
            updated_at=now,
        )
        session.add(rec)
    else:
        rec.value = merged_payload
        rec.updated_at = now
    await session.flush()
    return merged_payload


async def retry_failed_transitions(
    session: AsyncSession,
    *,
    reason: str,
    include_permanent: bool = False,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> int:
    """Reset failed scheduled transitions back to pending status for re-evaluation.

    Transient errors (locks, timeouts) are reset by default. Permanent errors
    (e.g., deleted target plan) require include_permanent=True.
    """
    if not reason or not reason.strip():
        raise ValueError("Administrative justification reason is mandatory")

    stmt = select(ScheduledSubscriptionTransitionDB).where(
        ScheduledSubscriptionTransitionDB.status == "failed"
    )
    failed_items = (await session.execute(stmt)).scalars().all()
    count = 0
    now = _utcnow()
    for item in failed_items:
        meta = dict(item.metadata_json or {})
        err_cat = meta.get("error_category", "transient")
        if err_cat == "permanent" and not include_permanent:
            logger.info("Skipping permanent failure transition %s during standard retry", item.id)
            continue

        item.status = "pending"
        meta["retry_requested_at"] = now.isoformat()
        meta["retry_reason"] = reason.strip()
        item.metadata_json = meta
        item.updated_at = now
        count += 1

    if count > 0:
        await log_commercial_audit(
            session,
            action="scheduler_failed_transitions_reset",
            tenant_id=None,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason.strip(),
            details={"reset_count": count, "include_permanent": include_permanent},
        )
        await session.flush()
    return count


async def process_due_subscription_transitions(
    session: AsyncSession,
    *,
    as_of: Optional[datetime] = None,
    enforce_rollout_guard: bool = False,
    allowed_tenant_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Process all due scheduled transitions idempotently with transactional locking."""
    now = as_of or _utcnow()

    cfg: Optional[dict[str, Any]] = None
    if enforce_rollout_guard:
        cfg = await get_scheduler_config(session)
        if cfg.get("paused"):
            logger.info("Subscription scheduler is currently paused; skipping scan.")
            return []

    # PostgreSQL safe locking to prevent concurrent double-execution
    bind = session.bind
    is_pg = bind.dialect.name == "postgresql" if bind else True

    stmt = select(ScheduledSubscriptionTransitionDB).where(
        ScheduledSubscriptionTransitionDB.status == "pending",
        ScheduledSubscriptionTransitionDB.scheduled_at <= now,
    )
    if is_pg:
        stmt = stmt.with_for_update(skip_locked=True)

    due_transitions = (await session.execute(stmt)).scalars().all()
    executed_results = []

    for trans in due_transitions:
        # Check rollout guard: strictly server-authoritative allowlist (no prefix heuristics)
        if enforce_rollout_guard and cfg:
            mode = cfg.get("rollout_mode", "allowlist_only")
            allowlist = set(cfg.get("tenant_allowlist") or [])

            # Explicit caller override has highest precedence
            if allowed_tenant_ids and trans.tenant_id in allowed_tenant_ids:
                pass
            elif mode in ("allowlist_only", "test_tenants_only"):
                # Strict allowlist: No name/prefix inference permitted!
                if trans.tenant_id not in allowlist:
                    logger.info(
                        "Skipping transition %s for tenant %s: tenant not in authoritative allowlist (mode=%s)",
                        trans.id,
                        trans.tenant_id,
                        mode,
                    )
                    continue
            elif mode == "all_tenants":
                # Grandfathered protection: legacy_active accounts cannot be mutated automatically unless explicitly allowlisted
                sub_check = (await session.execute(
                    select(TenantSubscriptionDB.status).where(TenantSubscriptionDB.tenant_id == trans.tenant_id)
                )).scalar_one_or_none()
                if sub_status := sub_check:
                    if sub_status == "legacy_active" and trans.tenant_id not in allowlist:
                        logger.warning(
                            "Skipping transition %s for grandfathered legacy_active tenant %s in all_tenants mode",
                            trans.id,
                            trans.tenant_id,
                        )
                        continue
            else:
                logger.warning("Unrecognized rollout_mode '%s'; defaulting to deny", mode)
                continue

        sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == trans.tenant_id)
        if is_pg:
            sub_stmt = sub_stmt.with_for_update()
        sub = (await session.execute(sub_stmt)).scalars().first()

        if not sub:
            trans.status = "failed"
            trans.metadata_json = dict(
                trans.metadata_json or {},
                error="Subscription not found",
                error_category="permanent",
            )
            trans.updated_at = now
            continue

        if trans.transition_type == "cancel":
            sub.status = "cancelled"
            sub.updated_at = now
        else:
            plan = await session.get(CommercialPlanDB, trans.target_plan_id)
            if not plan:
                trans.status = "failed"
                trans.metadata_json = dict(
                    trans.metadata_json or {},
                    error="Target plan does not exist",
                    error_category="permanent",
                )
                trans.updated_at = now
                continue

            sub.plan_id = plan.id
            sub.status = "active"
            sub.current_period_start = trans.scheduled_at
            sub.current_period_end = calculate_next_period_end(trans.scheduled_at, plan.billing_interval)
            sub.updated_at = now

        trans.status = "executed"
        trans.executed_at = now
        trans.updated_at = now

        await log_commercial_audit(
            session,
            action="scheduled_transition_executed",
            tenant_id=trans.tenant_id,
            actor_user_id=trans.actor_user_id,
            actor_role=trans.actor_role,
            reason=f"Executed scheduled {trans.transition_type} transition",
            details={
                "transition_id": trans.id,
                "transition_type": trans.transition_type,
                "target_plan_id": trans.target_plan_id,
                "scheduled_at": trans.scheduled_at.isoformat(),
                "executed_at": now.isoformat(),
            },
        )

        executed_results.append({
            "transition_id": trans.id,
            "tenant_id": trans.tenant_id,
            "transition_type": trans.transition_type,
            "target_plan_id": trans.target_plan_id,
            "status": "executed",
        })

    await session.flush()
    return executed_results


# ── Grace-Period & Suspension Policy (TASK 9) ──


async def enter_grace_period(
    session: AsyncSession,
    *,
    tenant_id: str,
    grace_days: int,
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> TenantSubscriptionDB:
    """Transition a tenant subscription into grace_period state with duration and reason."""
    if grace_days < 1 or grace_days > 90:
        raise ValueError("grace_days must be between 1 and 90 days")
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for entering grace period")

    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()
    if not sub:
        raise ValueError(f"Tenant '{tenant_id}' has no subscription record")

    validate_state_transition(sub.status, "grace_period")
    now = _utcnow()
    exp = now + timedelta(days=grace_days)
    old_status = sub.status

    sub.status = "grace_period"
    sub.updated_at = now
    meta = dict(sub.metadata_json or {})
    meta["grace_period"] = {
        "started_at": now.isoformat(),
        "expires_at": exp.isoformat(),
        "grace_days": grace_days,
        "reason": reason.strip(),
        "previous_status": old_status,
    }
    sub.metadata_json = meta

    await log_commercial_audit(
        session,
        action="grace_period_entered",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={
            "grace_days": grace_days,
            "grace_expires_at": exp.isoformat(),
            "previous_status": old_status,
        },
    )

    await session.flush()
    return sub


async def exit_grace_period(
    session: AsyncSession,
    *,
    tenant_id: str,
    new_status: str = "active",
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> TenantSubscriptionDB:
    """Exit grace period and transition to active, cancelled, or expired status."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for exiting grace period")

    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()
    if not sub:
        raise ValueError(f"Tenant '{tenant_id}' has no subscription record")
    if sub.status != "grace_period":
        raise ValueError(f"Tenant subscription is not in grace_period (current: '{sub.status}')")

    validate_state_transition("grace_period", new_status)
    now = _utcnow()
    sub.status = new_status
    sub.updated_at = now

    meta = dict(sub.metadata_json or {})
    if "grace_period" in meta:
        meta["grace_period"]["exited_at"] = now.isoformat()
        meta["grace_period"]["exit_reason"] = reason.strip()
    sub.metadata_json = meta

    await log_commercial_audit(
        session,
        action="grace_period_exited",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={"new_status": new_status},
    )

    await session.flush()
    return sub


async def suspend_subscription(
    session: AsyncSession,
    *,
    tenant_id: str,
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> TenantSubscriptionDB:
    """Immediately suspend tenant commercial access with mandatory reason."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for suspending a subscription")

    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()
    if not sub:
        raise ValueError(f"Tenant '{tenant_id}' has no subscription record")

    validate_state_transition(sub.status, "suspended")
    now = _utcnow()
    old_status = sub.status

    sub.status = "suspended"
    sub.updated_at = now
    meta = dict(sub.metadata_json or {})
    meta["suspension"] = {
        "suspended_at": now.isoformat(),
        "reason": reason.strip(),
        "previous_status": old_status,
    }
    meta["service_access_state"] = "suspended"
    sub.metadata_json = meta

    await log_commercial_audit(
        session,
        action="subscription_suspended",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={"previous_status": old_status},
    )

    await session.flush()
    return sub


async def unsuspend_subscription(
    session: AsyncSession,
    *,
    tenant_id: str,
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> TenantSubscriptionDB:
    """Lift suspension and restore previous subscription status."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for lifting a subscription suspension")

    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()
    if not sub:
        raise ValueError(f"Tenant '{tenant_id}' has no subscription record")
    if sub.status != "suspended":
        raise ValueError(f"Tenant subscription is not suspended (current: '{sub.status}')")

    now = _utcnow()
    meta = dict(sub.metadata_json or {})
    prev_status = meta.get("suspension", {}).get("previous_status", "active")
    validate_state_transition("suspended", prev_status)

    sub.status = prev_status
    sub.updated_at = now
    if "suspension" in meta:
        meta["suspension"]["unsuspended_at"] = now.isoformat()
        meta["suspension"]["unsuspend_reason"] = reason.strip()
    meta["service_access_state"] = "active"
    sub.metadata_json = meta

    await log_commercial_audit(
        session,
        action="subscription_unsuspended",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={"restored_status": prev_status},
    )

    await session.flush()
    return sub


# ── Subscription Cancellation & Renewal (TASK 7) ──


async def cancel_subscription(
    session: AsyncSession,
    *,
    tenant_id: str,
    immediate: bool = False,
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> TenantSubscriptionDB:
    """Cancel subscription immediately or schedule cancellation for current period end."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for subscription cancellation")

    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()
    if not sub:
        raise ValueError(f"Tenant '{tenant_id}' has no subscription record")

    now = _utcnow()

    if immediate:
        validate_state_transition(sub.status, "cancelled")
        sub.status = "cancelled"
        sub.updated_at = now
        meta = dict(sub.metadata_json or {})
        meta["service_access_state"] = "cancelled"
        meta["cancelled_at"] = now.isoformat()
        meta["cancellation_reason"] = reason.strip()
        sub.metadata_json = meta

        await log_commercial_audit(
            session,
            action="subscription_cancelled_immediate",
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason.strip(),
            details={"immediate": True},
        )
    else:
        # Schedule at period end
        await schedule_plan_transition(
            session,
            tenant_id=tenant_id,
            target_plan_id=None,
            transition_type="cancel",
            scheduled_at=sub.current_period_end,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason.strip(),
        )

    await session.flush()
    return sub


async def renew_subscription(
    session: AsyncSession,
    *,
    tenant_id: str,
    interval: str = "monthly",
    custom_days: Optional[int] = None,
    payment_verification: str = "manual_grant",
    reason: str,
    actor_user_id: Optional[str] = None,
    actor_role: str = "system_admin",
) -> TenantSubscriptionDB:
    """Administratively advance subscription period boundaries without fake invoice creation."""
    if not reason or not reason.strip():
        raise ValueError("Administrative reason is mandatory for subscription renewal")

    VALID_PAYMENT_VERIFICATION_STATES = {"none", "manual_grant", "deferred", "grandfathered", "external_verified"}
    if payment_verification not in VALID_PAYMENT_VERIFICATION_STATES:
        raise ValueError(f"Invalid payment verification status: '{payment_verification}'. Must be one of {sorted(VALID_PAYMENT_VERIFICATION_STATES)}")

    sub_stmt = select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tenant_id)
    sub = (await session.execute(sub_stmt)).scalars().first()
    if not sub:
        raise ValueError(f"Tenant '{tenant_id}' has no subscription record")

    now = _utcnow()
    # If expired or in grace, start now; otherwise start from existing period end
    if sub.current_period_end and sub.current_period_end > now:
        new_start = sub.current_period_end
    else:
        new_start = now

    if custom_days is not None:
        if custom_days < 1 or custom_days > 365:
            raise ValueError("custom_days must be between 1 and 365")
        new_end = new_start + timedelta(days=custom_days)
    else:
        new_end = calculate_next_period_end(new_start, interval)

    sub.current_period_start = new_start
    sub.current_period_end = new_end
    sub.updated_at = now

    # Restore active if expired/grace
    if sub.status in ("expired", "grace_period", "scheduled_cancellation"):
        sub.status = "active"

    meta = dict(sub.metadata_json or {})
    meta["payment_verification_state"] = payment_verification
    meta["last_renewed_at"] = now.isoformat()
    meta["last_renewed_reason"] = reason.strip()
    sub.metadata_json = meta

    await log_commercial_audit(
        session,
        action="subscription_renewed",
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        reason=reason.strip(),
        details={
            "new_period_start": new_start.isoformat(),
            "new_period_end": new_end.isoformat() if new_end else None,
            "payment_verification_state": payment_verification,
        },
    )

    await session.flush()
    return sub


# ── Plan Change Preview & Quotations (TASK 12) ──


async def preview_plan_change(
    session: AsyncSession,
    *,
    tenant_id: str,
    target_plan_id: str,
) -> dict[str, Any]:
    """Preview differences between current plan and target plan before making mutations."""
    target_plan = await session.get(CommercialPlanDB, target_plan_id)
    if not target_plan:
        raise ValueError(f"Target plan ID '{target_plan_id}' does not exist")

    # Current effective limits
    curr_entitlements, sub, curr_plan = await resolve_effective_entitlements(session, tenant_id)

    # Target plan entitlements
    t_stmt = select(CommercialPlanEntitlementDB).where(CommercialPlanEntitlementDB.plan_id == target_plan_id)
    target_ents = {e.entitlement_key: e for e in (await session.execute(t_stmt)).scalars().all()}

    ent_diff = {}
    has_downgrade = False

    for k in CANONICAL_ENTITLEMENT_METRICS.keys():
        curr_e = curr_entitlements.get(k)
        t_e = target_ents.get(k)

        curr_val = curr_e.value_bool if curr_e and curr_e.value_type == "boolean" else (curr_e.value_int if curr_e else None)
        curr_unlim = curr_e.is_unlimited if curr_e else False

        t_val = t_e.value_bool if t_e and t_e.value_type == "boolean" else (t_e.value_int if t_e else None)
        t_unlim = t_e.is_unlimited if t_e else False

        change_type = "unchanged"
        if curr_unlim and not t_unlim:
            change_type = "decreased"
            has_downgrade = True
        elif not curr_unlim and t_unlim:
            change_type = "increased"
        elif curr_val != t_val:
            if isinstance(curr_val, (int, float)) and isinstance(t_val, (int, float)):
                if t_val > curr_val:
                    change_type = "increased"
                elif t_val < curr_val:
                    change_type = "decreased"
                    has_downgrade = True
            elif isinstance(curr_val, bool) and isinstance(t_val, bool):
                if t_val and not curr_val:
                    change_type = "increased"
                elif not t_val and curr_val:
                    change_type = "decreased"
                    has_downgrade = True

        ent_diff[k] = {
            "current": {"value": curr_val, "is_unlimited": curr_unlim},
            "target": {"value": t_val, "is_unlimited": t_unlim},
            "change_type": change_type,
        }

    # Price display
    target_meta = target_plan.metadata_json or {}
    is_unapproved = target_plan.price_cents is None or target_meta.get("pricing_status") == "unapproved_pending_commercial_review"
    pricing_display = "Pricing not configured" if is_unapproved else f"${target_plan.price_cents / 100:.2f} {target_plan.currency}"

    transition_type = "downgrade" if has_downgrade else "upgrade"
    rec_effective = "period_end" if has_downgrade else "immediate"

    return {
        "tenant_id": tenant_id,
        "current_plan": {
            "plan_code": curr_plan.plan_code if curr_plan else "default_free",
            "name": curr_plan.name if curr_plan else "MediaForge Free Default",
            "version": curr_plan.version if curr_plan else 1,
            "status": sub.status if sub else "unassigned",
        },
        "target_plan": {
            "id": target_plan.id,
            "plan_code": target_plan.plan_code,
            "name": target_plan.name,
            "version": target_plan.version,
            "tier_type": target_plan.tier_type,
            "billing_interval": target_plan.billing_interval,
            "status": target_plan.status,
            "pricing_display": pricing_display,
        },
        "transition_type": transition_type,
        "recommended_effective_at": rec_effective,
        "next_period_end": sub.current_period_end.isoformat() if (sub and sub.current_period_end) else None,
        "entitlement_diff": ent_diff,
        "pricing_display": pricing_display,
    }


async def preview_commercial_quotation(
    session: AsyncSession,
    *,
    tenant_id: str,
    plan_id: str,
    custom_overrides: Optional[dict[str, Any]] = None,
    discount_percent: Optional[float] = None,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Generate commercial quotation and pricing preview without financial side-effects."""
    plan = await session.get(CommercialPlanDB, plan_id)
    if not plan:
        raise ValueError(f"Plan ID '{plan_id}' does not exist")

    meta = plan.metadata_json or {}
    is_unapproved = plan.price_cents is None or meta.get("pricing_status") == "unapproved_pending_commercial_review"

    if is_unapproved:
        pricing_display = "Pricing not configured"
        is_approved = False
        base_price_cents = None
        effective_amount_cents = None
    else:
        is_approved = True
        base_price_cents = plan.price_cents
        discount_amount = round(base_price_cents * ((discount_percent or 0.0) / 100.0))
        effective_amount_cents = max(0, base_price_cents - discount_amount)
        pricing_display = f"${effective_amount_cents / 100:.2f} {plan.currency}"

    # Entitlement preview
    e_stmt = select(CommercialPlanEntitlementDB).where(CommercialPlanEntitlementDB.plan_id == plan_id)
    base_ents = {e.entitlement_key: e for e in (await session.execute(e_stmt)).scalars().all()}

    preview_entitlements = {}
    for k, spec in CANONICAL_ENTITLEMENT_METRICS.items():
        base_e = base_ents.get(k)
        val = base_e.value_bool if base_e and base_e.value_type == "boolean" else (base_e.value_int if base_e else spec["default"])
        unlim = base_e.is_unlimited if base_e else False

        # Apply custom override if present
        if custom_overrides and k in custom_overrides:
            ov = custom_overrides[k]
            val = ov.get("value", val)
            unlim = ov.get("is_unlimited", unlim)

        preview_entitlements[k] = {
            "value": val,
            "is_unlimited": unlim,
            "unit": spec["unit"],
        }

    return {
        "tenant_id": tenant_id,
        "plan_id": plan.id,
        "plan_code": plan.plan_code,
        "plan_name": plan.name,
        "tier_type": plan.tier_type,
        "billing_interval": plan.billing_interval,
        "currency": plan.currency,
        "base_price_cents": base_price_cents,
        "discount_percent": discount_percent,
        "effective_amount_cents": effective_amount_cents,
        "pricing_display": pricing_display,
        "is_approved_pricing": is_approved,
        "effective_entitlements": preview_entitlements,
        "notes": notes,
        "disclaimer": "Commercial quotation preview only. Not a bill or invoice. No payment method charged.",
    }
