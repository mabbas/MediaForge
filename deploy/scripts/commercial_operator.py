#!/usr/bin/env python3
"""Commercial Operator Administrative CLI (STEP 04-A3 & STEP 04-B1).

SECURITY BOUNDARY & OPERATIONAL GUIDELINES:
- This CLI is strictly an internal administrative tool meant for infrastructure operators.
- Must execute on the backend server or trusted management host with direct PostgreSQL credentials.
- It must NEVER be bundled, distributed, or exposed in the Electron desktop application, web client, or unauthenticated public network interfaces.
- All mutating commands require explicit `--reason` parameters to ensure full auditability in `gid.commercial_audit_logs`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import func, select
from api.config import get_api_settings
from api.database.connection import close_database, get_session_factory, init_database
from api.database.models import (
    CommercialPlanDB,
    ScheduledSubscriptionTransitionDB,
    TenantDB,
    TenantSubscriptionDB,
)
from api.services.enforcement_service import get_enforcement_config
from api.services.entitlement_service import (
    evaluate_tenant_quotas,
    extend_tenant_trial,
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


async def cmd_list_plans(factory, args):
    async with factory() as session:
        stmt = select(CommercialPlanDB).order_by(CommercialPlanDB.plan_code, CommercialPlanDB.version.desc())
        plans = (await session.execute(stmt)).scalars().all()
        print(f"\nFound {len(plans)} commercial plans:")
        print(f"{'Code':<20} {'Ver':<5} {'Status':<10} {'Tier':<8} {'Price':<16} {'Name'}")
        print("-" * 80)
        for p in plans:
            price_str = f"${p.price_cents / 100:.2f}" if p.price_cents is not None else "Unset"
            if p.metadata_json and p.metadata_json.get("pricing_status") == "unapproved_pending_commercial_review":
                price_str += " (unapproved)"
            print(f"{p.plan_code:<20} {p.version:<5} {p.status:<10} {p.tier_type:<8} {price_str:<16} {p.name}")


async def cmd_tenant_status(factory, args):
    async with factory() as session:
        res = await evaluate_tenant_quotas(session, args.tenant_id)
        print(f"\nCommercial Status for Tenant: {args.tenant_id}")
        print(f"Plan: {res.plan_name} ({res.plan_code} v{res.plan_version}) [{res.tier_type}]")
        print(f"Subscription Status: {res.subscription_status.upper()}")
        print(f"Current Period: {res.current_period_start} to {res.current_period_end}")
        if res.is_trial:
            print(f"Trial Remaining: {res.trial_days_remaining} days (expires {res.trial_expires_at})")
        if res.grace_period_expires_at:
            print(f"Grace Period: expires {res.grace_period_expires_at} ({res.grace_period_remaining_days} days remaining)")
        if res.is_suspended:
            print(f"SUSPENDED: {res.suspension_reason}")
        if res.scheduled_transition:
            st = res.scheduled_transition
            print(f"Scheduled Transition: {st['transition_type'].upper()} to {st['target_plan_code']} on {st['scheduled_at']}")


async def cmd_preview_change(factory, args):
    async with factory() as session:
        res = await preview_plan_change(session, tenant_id=args.tenant_id, target_plan_id=args.target_plan_id)
        print(f"\nPlan Change Preview for Tenant: {args.tenant_id}")
        print(f"Current: {res['current_plan']['name']} ({res['current_plan']['plan_code']})")
        print(f"Target:  {res['target_plan']['name']} ({res['target_plan']['plan_code']})")
        print(f"Transition Type: {res['transition_type'].upper()} (Recommended: {res['recommended_effective_at']})")
        print(f"Pricing: {res['pricing_display']}")
        print("\nEntitlement Differences:")
        for k, v in res["entitlement_diff"].items():
            if v["change_type"] != "unchanged":
                print(f"  - {k:<25}: {v['current']['value']} -> {v['target']['value']} ({v['change_type']})")


async def cmd_process_due(factory, args):
    async with factory() as session:
        res = await process_due_subscription_transitions(session)
        await session.commit()
        print(f"\nProcessed {len(res)} due transitions:")
        for r in res:
            print(f"  - Transition {r['transition_id']}: Tenant {r['tenant_id']} {r['transition_type']} executed")


async def cmd_scheduler_status(factory, args):
    async with factory() as session:
        cfg = await get_scheduler_config(session)
        state = await get_scheduler_state(session)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        due_count = (await session.execute(
            select(func.count()).select_from(ScheduledSubscriptionTransitionDB)
            .where(
                ScheduledSubscriptionTransitionDB.status == "pending",
                ScheduledSubscriptionTransitionDB.scheduled_at <= now,
            )
        )).scalar() or 0

        failed_count = (await session.execute(
            select(func.count()).select_from(ScheduledSubscriptionTransitionDB)
            .where(ScheduledSubscriptionTransitionDB.status == "failed")
        )).scalar() or 0

        print("\n" + "=" * 65)
        print("          COMMERCIAL SUBSCRIPTION SCHEDULER STATUS")
        print("=" * 65)
        print(f"State:               {'PAUSED' if cfg.get('paused') else 'ACTIVE'}")
        if cfg.get("paused"):
            print(f"Pause Reason:        {cfg.get('reason') or 'No reason recorded'}")
            print(f"Paused By:           {cfg.get('updated_by_user_id') or 'system'}")
        print(f"Rollout Mode:        {cfg.get('rollout_mode')}")
        print(f"Test Prefixes:       {', '.join(cfg.get('test_tenant_prefixes', []))}")
        print(f"Allowlist Tenants:   {len(cfg.get('tenant_allowlist', []))}")
        print(f"Poll Interval:       {cfg.get('poll_interval_seconds')}s")
        print("-" * 65)
        print(f"Last Worker Run:     {state.get('last_scan_at') or 'Never'}")
        print(f"Worker Status:       {state.get('status', 'idle')}")
        print(f"Last Batch Executed: {state.get('last_executed_count', 0)} transitions")
        print(f"Transitions Due Now: {due_count}")
        print(f"Failed Transitions:  {failed_count}")
        print("=" * 65 + "\n")


async def cmd_pause_scheduler(factory, args):
    if not args.reason or not args.reason.strip():
        print("Error: --reason is mandatory to pause the scheduler.", file=sys.stderr)
        sys.exit(1)
    async with factory() as session:
        cfg = await update_scheduler_config(
            session,
            paused=True,
            reason=args.reason.strip(),
            actor_role="operator_cli",
        )
        await session.commit()
        print(f"\n[OK] Scheduler paused successfully.")
        print(f"Reason: {args.reason.strip()}")
        print(f"Updated at: {cfg.get('updated_at')}\n")


async def cmd_resume_scheduler(factory, args):
    if not args.reason or not args.reason.strip():
        print("Error: --reason is mandatory to resume the scheduler.", file=sys.stderr)
        sys.exit(1)
    async with factory() as session:
        cfg = await update_scheduler_config(
            session,
            paused=False,
            reason=args.reason.strip(),
            actor_role="operator_cli",
        )
        await session.commit()
        print(f"\n[OK] Scheduler resumed successfully.")
        print(f"Reason: {args.reason.strip()}")
        print(f"Rollout Mode: {cfg.get('rollout_mode')}")
        print(f"Updated at: {cfg.get('updated_at')}\n")


async def cmd_retry_failed(factory, args):
    if not args.reason or not args.reason.strip():
        print("Error: --reason is mandatory to retry failed transitions.", file=sys.stderr)
        sys.exit(1)
    async with factory() as session:
        count = await retry_failed_transitions(
            session,
            reason=args.reason.strip(),
            actor_role="operator_cli",
        )
        await session.commit()
        print(f"\n[OK] Reset {count} failed transitions back to pending status.")
        print(f"Reason: {args.reason.strip()}\n")


async def cmd_dashboard_stats(factory, args):
    async with factory() as session:
        total_tenants = (await session.execute(select(func.count()).select_from(TenantDB))).scalar() or 0

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

        print("\n" + "=" * 65)
        print("             MEDIAFORGE COMMERCIAL DASHBOARD METRICS")
        print("=" * 65)
        print(f"Total Customer Tenants:      {total_tenants}")
        print(f"Active Commercial Plans:     {active_plans}")
        print(f"Pending Transitions:         {pending_tr}")
        print("-" * 65)
        print(f"Global Enforcement Mode:     {enforce_cfg.get('mode', 'disabled').upper()}")
        print(f"Tenant Allowlist Count:      {len(enforce_cfg.get('tenant_allowlist', []))}")
        print("-" * 65)
        print(f"Scheduler Status:            {'PAUSED' if sched_cfg.get('paused') else 'ACTIVE'}")
        print(f"Scheduler Rollout Mode:      {sched_cfg.get('rollout_mode')}")
        print(f"Scheduler Last Scan:         {sched_state.get('last_scan_at') or 'Never'}")
        print(f"Scheduler Last Executed:     {sched_state.get('last_executed_count', 0)}")
        print("=" * 65 + "\n")


async def _run_command(handler, args):
    settings = get_api_settings()
    await init_database(settings.database_url)
    factory = get_session_factory()
    try:
        await handler(factory, args)
    finally:
        await close_database()


def main():
    parser = argparse.ArgumentParser(description="MediaForge Commercial Operator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list-plans
    subparsers.add_parser("list-plans", help="List commercial plan catalog")

    # tenant-status
    p_stat = subparsers.add_parser("tenant-status", help="Inspect tenant commercial subscription")
    p_stat.add_argument("tenant_id", help="Target tenant ID")

    # preview-change
    p_prev = subparsers.add_parser("preview-change", help="Preview plan change")
    p_prev.add_argument("tenant_id", help="Target tenant ID")
    p_prev.add_argument("target_plan_id", help="Target plan ID")

    # process-due
    subparsers.add_parser("process-due", help="Process all due scheduled transitions")

    # scheduler-status
    subparsers.add_parser("scheduler-status", help="Display background transition scheduler status and metrics")

    # pause-scheduler
    p_pause = subparsers.add_parser("pause-scheduler", help="Emergency pause for automated transition scheduler")
    p_pause.add_argument("--reason", required=True, help="Mandatory administrative justification")

    # resume-scheduler
    p_resume = subparsers.add_parser("resume-scheduler", help="Resume automated transition scheduler")
    p_resume.add_argument("--reason", required=True, help="Mandatory administrative justification")

    # retry-failed
    p_retry = subparsers.add_parser("retry-failed", help="Reset failed scheduled transitions to pending")
    p_retry.add_argument("--reason", required=True, help="Mandatory administrative justification")

    # dashboard-stats
    subparsers.add_parser("dashboard-stats", help="Display platform commercial metrics overview")

    args = parser.parse_args()

    handlers = {
        "list-plans": cmd_list_plans,
        "tenant-status": cmd_tenant_status,
        "preview-change": cmd_preview_change,
        "process-due": cmd_process_due,
        "scheduler-status": cmd_scheduler_status,
        "pause-scheduler": cmd_pause_scheduler,
        "resume-scheduler": cmd_resume_scheduler,
        "retry-failed": cmd_retry_failed,
        "dashboard-stats": cmd_dashboard_stats,
    }

    handler = handlers.get(args.command)
    if handler:
        asyncio.run(_run_command(handler, args))


if __name__ == "__main__":
    main()
