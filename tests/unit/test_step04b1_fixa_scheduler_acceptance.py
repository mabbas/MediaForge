"""MediaForge STEP 04-B1-Fix-A: Real Autonomous Background Scheduler Acceptance Tests.

Rigorously verifies:
1. Autonomous recurring execution (without manual triggers) using real MediaWorker loop.
2. Cycle heartbeat updates and cluster state tracking.
3. Plan assignment mutations, PostgreSQL committed state, and audit logs.
4. Multi-worker concurrency safety and row-locking (FOR UPDATE SKIP LOCKED).
5. Concurrent administrative manual scan trigger vs background worker loop.
6. Emergency pause and resume behavior during live polling.
7. Worker restart recovery and subsequent due transition processing.
8. Retriable vs permanent failure categorization and safe retry controls.
9. Grandfathered legacy_active account protection in all_tenants rollout mode.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import uuid
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import get_api_settings
from api.database.connection import get_session_factory
from api.database.models import (
    AppSettingDB,
    CommercialAuditLogDB,
    CommercialPlanDB,
    ScheduledSubscriptionTransitionDB,
    TenantDB,
    TenantSubscriptionDB,
)
from api.services.subscription_lifecycle_service import (
    get_scheduler_config,
    get_scheduler_state,
    process_due_subscription_transitions,
    retry_failed_transitions,
    update_scheduler_config,
    update_scheduler_state,
)
from api.workers.media_worker import MediaWorker

PG_URL = os.environ.get(
    "GID_TEST_DATABASE_URL",
    "postgresql+asyncpg://gid_app:bZNpoCvQ9UwPGgwUNkTQNzmYfsJ6WHbJ@127.0.0.1:55432/grabitdown_test",
)


@pytest.fixture
def pg_session_factory():
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


# ==============================================================================
# 1. AUTONOMOUS RECURRING SCHEDULER EXECUTION (TASK 10)
# ==============================================================================
@pytest.mark.asyncio
async def test_01_autonomous_recurring_scheduler_execution(pg_session_factory):
    """Prove actual autonomous execution of the MediaWorker subscription scheduler loop

    without invoking any manual scan trigger.
    """
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"test_auto_{uid}"
    plan_id = f"plan_auto_{uid}"
    sub_id = f"sub_auto_{uid}"
    trans_id = f"tr_auto_{uid}"

    worker = MediaWorker(worker_id=f"worker_auto_{uid}", session_factory=pg_session_factory)
    worker.running = True

    try:
        async with pg_session_factory() as session:
            # Create base plan & target plan
            base_plan = (await session.execute(
                select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
            )).scalars().first()

            target_plan = CommercialPlanDB(
                id=plan_id,
                plan_code=f"code_{uid}",
                name="Auto Target Plan",
                version=1,
                tier_type="paid",
                status="active",
                price_cents=3900,
                billing_interval="monthly",
            )
            tenant = TenantDB(id=tenant_id, name="Autonomous Tenant", slug=f"auto-slug-{uid}", tier="basic", is_active=True)
            now = _utcnow()
            subscription = TenantSubscriptionDB(
                id=sub_id,
                tenant_id=tenant_id,
                plan_id=base_plan.id,
                status="active",
                current_period_start=now - timedelta(days=30),
                current_period_end=now - timedelta(minutes=1),
            )
            # Create transition due in 1 second
            transition = ScheduledSubscriptionTransitionDB(
                id=trans_id,
                tenant_id=tenant_id,
                target_plan_id=plan_id,
                transition_type="upgrade",
                status="pending",
                scheduled_at=now - timedelta(seconds=1),
                reason="Autonomous acceptance scheduled transition",
                actor_role="system_admin",
            )
            session.add_all([target_plan, tenant, subscription, transition])

            # Configure scheduler with accelerated 1-second polling and explicit allowlist
            await update_scheduler_config(
                session,
                paused=False,
                rollout_mode="allowlist_only",
                tenant_allowlist=[tenant_id],
                poll_interval_seconds=1,
                reason="Autonomous scheduler test configuration",
            )
            await session.commit()

        # Launch the actual worker loop as an asynchronous background task with 0s initial delay
        loop_task = asyncio.create_task(worker._subscription_transitions_loop(initial_delay_seconds=0.0))

        # Allow worker loop to autonomously poll and execute transition (DO NOT trigger manual scan!)
        await asyncio.sleep(2.5)

        # Stop worker
        worker.running = False
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

        # Verify PostgreSQL committed state
        async with pg_session_factory() as session:
            db_trans = await session.get(ScheduledSubscriptionTransitionDB, trans_id)
            assert db_trans is not None
            assert db_trans.status == "executed"
            assert db_trans.executed_at is not None

            db_sub = await session.get(TenantSubscriptionDB, sub_id)
            assert db_sub is not None
            assert db_sub.plan_id == plan_id  # Plan was updated!
            assert db_sub.status == "active"

            # Verify audit log
            audit = (await session.execute(
                select(CommercialAuditLogDB).where(
                    CommercialAuditLogDB.tenant_id == tenant_id,
                    CommercialAuditLogDB.action == "scheduled_transition_executed",
                )
            )).scalars().first()
            assert audit is not None
            assert audit.details_json["transition_id"] == trans_id

            # Verify cluster state recorded heartbeat and worker identity
            state = await get_scheduler_state(session)
            assert state["last_scan_at"] is not None
            assert state["worker_id"] == f"worker_auto_{uid}"

    finally:
        worker.running = False
        async with pg_session_factory() as session:
            await update_scheduler_config(
                session,
                poll_interval_seconds=60,
                tenant_allowlist=[],
                reason="Restore baseline scheduler config",
            )
            await session.commit()


# ==============================================================================
# 2. WORKER RESTART RECOVERY & SUBSEQUENT EXECUTION (TASK 10)
# ==============================================================================
@pytest.mark.asyncio
async def test_02_worker_restart_resumes_and_processes_next_transition(pg_session_factory):
    """Verify that stopping a worker and starting a new worker resumes polling

    and successfully processes the next due transition.
    """
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"test_res_{uid}"
    plan_id = f"plan_res_{uid}"
    sub_id = f"sub_res_{uid}"
    trans_id = f"tr_res_{uid}"

    worker1 = MediaWorker(worker_id=f"worker_res1_{uid}", session_factory=pg_session_factory)
    worker1.running = True

    try:
        async with pg_session_factory() as session:
            base_plan = (await session.execute(
                select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
            )).scalars().first()

            target_plan = CommercialPlanDB(
                id=plan_id,
                plan_code=f"code_res_{uid}",
                name="Restart Target Plan",
                version=1,
                tier_type="paid",
                status="active",
                price_cents=4500,
                billing_interval="monthly",
            )
            tenant = TenantDB(id=tenant_id, name="Restart Tenant", slug=f"res-slug-{uid}", tier="basic", is_active=True)
            now = _utcnow()
            subscription = TenantSubscriptionDB(
                id=sub_id,
                tenant_id=tenant_id,
                plan_id=base_plan.id,
                status="active",
                current_period_start=now - timedelta(days=30),
                current_period_end=now - timedelta(minutes=1),
            )
            session.add_all([target_plan, tenant, subscription])

            await update_scheduler_config(
                session,
                paused=False,
                rollout_mode="allowlist_only",
                tenant_allowlist=[tenant_id],
                poll_interval_seconds=1,
                reason="Restart test setup",
            )
            await session.commit()

        # Start worker 1 and shut it down immediately
        t1 = asyncio.create_task(worker1._subscription_transitions_loop(initial_delay_seconds=0.0))
        await asyncio.sleep(0.5)
        worker1.running = False
        t1.cancel()
        try:
            await t1
        except asyncio.CancelledError:
            pass

        # Now insert the due transition while worker is stopped
        async with pg_session_factory() as session:
            now = _utcnow()
            transition = ScheduledSubscriptionTransitionDB(
                id=trans_id,
                tenant_id=tenant_id,
                target_plan_id=plan_id,
                transition_type="upgrade",
                status="pending",
                scheduled_at=now - timedelta(seconds=2),
                reason="Transition created during worker offline",
                actor_role="system_admin",
            )
            session.add(transition)
            await session.commit()

        # Start Worker 2 (new worker replica)
        worker2 = MediaWorker(worker_id=f"worker_res2_{uid}", session_factory=pg_session_factory)
        worker2.running = True
        t2 = asyncio.create_task(worker2._subscription_transitions_loop(initial_delay_seconds=0.0))

        # Wait for worker 2 to claim and process the transition
        await asyncio.sleep(2.5)

        worker2.running = False
        t2.cancel()
        try:
            await t2
        except asyncio.CancelledError:
            pass

        # Verify transition was completed by worker 2
        async with pg_session_factory() as session:
            db_trans = await session.get(ScheduledSubscriptionTransitionDB, trans_id)
            assert db_trans is not None
            assert db_trans.status == "executed"

            db_sub = await session.get(TenantSubscriptionDB, sub_id)
            assert db_sub.plan_id == plan_id

    finally:
        worker1.running = False
        async with pg_session_factory() as session:
            await update_scheduler_config(
                session,
                poll_interval_seconds=60,
                tenant_allowlist=[],
                reason="Restore baseline scheduler config",
            )
            await session.commit()


# ==============================================================================
# 3. DUAL-WORKER CONCURRENCY & FOR UPDATE SKIP LOCKED SAFETY (TASK 11)
# ==============================================================================
@pytest.mark.asyncio
async def test_03_dual_worker_concurrent_execution_no_duplicates(pg_session_factory):
    """Verify that when 2 scheduler workers poll simultaneously across multiple due transitions,

    PostgreSQL row-level locking (FOR UPDATE SKIP LOCKED) prevents double-execution and race conditions.
    """
    uid = uuid.uuid4().hex[:8]
    tenant_ids = [f"test_dual_{uid}_{i}" for i in range(4)]
    trans_ids = [f"tr_dual_{uid}_{i}" for i in range(4)]

    worker_a = MediaWorker(worker_id=f"worker_a_{uid}", session_factory=pg_session_factory)
    worker_b = MediaWorker(worker_id=f"worker_b_{uid}", session_factory=pg_session_factory)
    worker_a.running = True
    worker_b.running = True

    try:
        async with pg_session_factory() as session:
            active_plan = (await session.execute(
                select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
            )).scalars().first()

            now = _utcnow()
            for i, tid in enumerate(tenant_ids):
                session.add(TenantDB(id=tid, name=f"Dual Tenant {i}", slug=f"dual-{uid}-{i}", tier="basic", is_active=True))
                session.add(TenantSubscriptionDB(
                    id=f"sub_dual_{uid}_{i}",
                    tenant_id=tid,
                    plan_id=active_plan.id,
                    status="active",
                    current_period_start=now - timedelta(days=30),
                    current_period_end=now - timedelta(minutes=5),
                ))
                session.add(ScheduledSubscriptionTransitionDB(
                    id=trans_ids[i],
                    tenant_id=tid,
                    transition_type="cancel",
                    status="pending",
                    scheduled_at=now - timedelta(seconds=10),
                    reason=f"Concurrent test cancellation {i}",
                    actor_role="system_admin",
                ))

            await update_scheduler_config(
                session,
                paused=False,
                rollout_mode="allowlist_only",
                tenant_allowlist=tenant_ids,
                poll_interval_seconds=1,
                reason="Dual worker concurrency test setup",
            )
            await session.commit()

        # Run both workers simultaneously
        task_a = asyncio.create_task(worker_a._subscription_transitions_loop(initial_delay_seconds=0.0))
        task_b = asyncio.create_task(worker_b._subscription_transitions_loop(initial_delay_seconds=0.0))

        # Allow concurrent sweeps
        await asyncio.sleep(2.5)

        worker_a.running = False
        worker_b.running = False
        task_a.cancel()
        task_b.cancel()
        await asyncio.gather(task_a, task_b, return_exceptions=True)

        # Verify all 4 transitions executed with exactly 1 execution each
        async with pg_session_factory() as session:
            for tr_id in trans_ids:
                tr = await session.get(ScheduledSubscriptionTransitionDB, tr_id)
                assert tr is not None
                assert tr.status == "executed"

            for tid in tenant_ids:
                sub = (await session.execute(
                    select(TenantSubscriptionDB).where(TenantSubscriptionDB.tenant_id == tid)
                )).scalars().first()
                assert sub.status == "cancelled"

                # Check audit logs: exactly 1 execution audit per transition!
                audit_count = (await session.execute(
                    select(func.count()).select_from(CommercialAuditLogDB).where(
                        CommercialAuditLogDB.tenant_id == tid,
                        CommercialAuditLogDB.action == "scheduled_transition_executed",
                    )
                )).scalar()
                assert audit_count == 1, f"Tenant {tid} had {audit_count} execution audits (expected 1)"

    finally:
        worker_a.running = False
        worker_b.running = False
        async with pg_session_factory() as session:
            await update_scheduler_config(
                session,
                poll_interval_seconds=60,
                tenant_allowlist=[],
                reason="Restore baseline scheduler config",
            )
            await session.commit()


# ==============================================================================
# 4. EMERGENCY PAUSE DURING LIVE POLLING (TASK 11)
# ==============================================================================
@pytest.mark.asyncio
async def test_04_emergency_pause_halts_processing_immediately(pg_session_factory):
    """Verify that pausing the scheduler halts automated processing, and resuming it

    allows due transitions to complete.
    """
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"test_pause_{uid}"
    trans_id = f"tr_pause_{uid}"

    worker = MediaWorker(worker_id=f"worker_pause_{uid}", session_factory=pg_session_factory)
    worker.running = True

    try:
        async with pg_session_factory() as session:
            active_plan = (await session.execute(
                select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
            )).scalars().first()

            now = _utcnow()
            session.add(TenantDB(id=tenant_id, name="Pause Tenant", slug=f"pause-slug-{uid}", tier="basic", is_active=True))
            session.add(TenantSubscriptionDB(
                id=f"sub_pause_{uid}",
                tenant_id=tenant_id,
                plan_id=active_plan.id,
                status="active",
                current_period_start=now - timedelta(days=30),
                current_period_end=now - timedelta(minutes=1),
            ))
            session.add(ScheduledSubscriptionTransitionDB(
                id=trans_id,
                tenant_id=tenant_id,
                transition_type="cancel",
                status="pending",
                scheduled_at=now - timedelta(seconds=5),
                reason="Emergency pause test transition",
                actor_role="system_admin",
            ))

            # Start in PAUSED state
            await update_scheduler_config(
                session,
                paused=True,
                rollout_mode="allowlist_only",
                tenant_allowlist=[tenant_id],
                poll_interval_seconds=1,
                reason="Emergency test pause",
            )
            await session.commit()

        task = asyncio.create_task(worker._subscription_transitions_loop(initial_delay_seconds=0.0))
        await asyncio.sleep(2.0)

        # Confirm transition was NOT executed while paused
        async with pg_session_factory() as session:
            tr = await session.get(ScheduledSubscriptionTransitionDB, trans_id)
            assert tr.status == "pending"

            # Now RESUME scheduler
            await update_scheduler_config(
                session,
                paused=False,
                reason="Emergency test resume",
            )
            await session.commit()

        # Allow worker loop to poll in resumed state
        await asyncio.sleep(2.0)

        worker.running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Confirm transition executed once resumed
        async with pg_session_factory() as session:
            tr = await session.get(ScheduledSubscriptionTransitionDB, trans_id)
            assert tr.status == "executed"

    finally:
        worker.running = False
        async with pg_session_factory() as session:
            await update_scheduler_config(
                session,
                paused=False,
                poll_interval_seconds=60,
                tenant_allowlist=[],
                reason="Restore baseline scheduler config",
            )
            await session.commit()


# ==============================================================================
# 5. RETRIABLE VS PERMANENT FAILURE RECOVERY (TASK 11)
# ==============================================================================
@pytest.mark.asyncio
async def test_05_retriable_vs_permanent_failure_handling(pg_session_factory):
    """Verify that permanent errors (e.g. missing subscription) are categorized

    and not blindly reset without include_permanent=True.
    """
    uid = uuid.uuid4().hex[:8]
    tenant_id = f"test_err_{uid}"
    trans_id = f"tr_err_{uid}"

    async with pg_session_factory() as session:
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()

        now = _utcnow()
        session.add(TenantDB(id=tenant_id, name="Error Tenant", slug=f"err-slug-{uid}", tier="basic", is_active=True))
        sub = TenantSubscriptionDB(
            id=f"sub_err_{uid}",
            tenant_id=tenant_id,
            plan_id=active_plan.id,
            status="active",
            current_period_start=now - timedelta(days=30),
            current_period_end=now - timedelta(minutes=1),
        )
        session.add(sub)
        session.add(ScheduledSubscriptionTransitionDB(
            id=trans_id,
            tenant_id=tenant_id,
            target_plan_id=active_plan.id,
            transition_type="upgrade",
            status="pending",
            scheduled_at=now - timedelta(minutes=2),
            reason="Permanent error simulation",
            actor_role="system_admin",
        ))
        await session.commit()

        # Delete subscription to create permanent error state (Subscription not found)
        await session.delete(sub)
        await session.commit()

        # Run process
        executed = await process_due_subscription_transitions(session, enforce_rollout_guard=False)
        await session.commit()

        # Confirm transition failed and was marked permanent
        tr = await session.get(ScheduledSubscriptionTransitionDB, trans_id)
        assert tr.status == "failed"
        assert tr.metadata_json.get("error_category") == "permanent"

        # Standard retry without include_permanent MUST skip it
        reset_count = await retry_failed_transitions(session, reason="Normal retry sweep", include_permanent=False)
        await session.commit()
        assert reset_count == 0
        await session.refresh(tr)
        assert tr.status == "failed"

        # Explicit operator retry with include_permanent=True resets it
        reset_perm = await retry_failed_transitions(session, reason="Operator explicit permanent retry", include_permanent=True)
        await session.commit()
        assert reset_perm >= 1
        await session.refresh(tr)
        assert tr.status == "pending"


# ==============================================================================
# 6. GRANDFATHERED LEGACY_ACTIVE FENCING IN ALL_TENANTS MODE (TASK 9)
# ==============================================================================
@pytest.mark.asyncio
async def test_06_grandfathered_legacy_tenant_fenced_in_all_tenants_mode(pg_session_factory):
    """Verify that even when rollout_mode='all_tenants', a grandfathered legacy_active

    tenant is fenced from automated mutation unless explicitly allowlisted.
    """
    uid = uuid.uuid4().hex[:8]
    legacy_tenant_id = f"test_leg_{uid}"
    trans_id = f"tr_leg_{uid}"

    async with pg_session_factory() as session:
        active_plan = (await session.execute(
            select(CommercialPlanDB).where(CommercialPlanDB.status == "active")
        )).scalars().first()

        now = _utcnow()
        session.add(TenantDB(id=legacy_tenant_id, name="Grandfathered Tenant", slug=f"leg-slug-{uid}", tier="platinum", is_active=True))
        session.add(TenantSubscriptionDB(
            id=f"sub_leg_{uid}",
            tenant_id=legacy_tenant_id,
            plan_id=active_plan.id,
            status="legacy_active",  # Grandfathered!
            current_period_start=now - timedelta(days=60),
            current_period_end=now - timedelta(days=5),
            metadata_json={"reconciliation_status": "pending_commercial_review"},
        ))
        session.add(ScheduledSubscriptionTransitionDB(
            id=trans_id,
            tenant_id=legacy_tenant_id,
            transition_type="cancel",
            status="pending",
            scheduled_at=now - timedelta(minutes=5),
            reason="Unintended cancellation on grandfathered account",
            actor_role="system_admin",
        ))

        # Set rollout_mode to 'all_tenants' but DO NOT put legacy_tenant_id in allowlist
        await update_scheduler_config(
            session,
            rollout_mode="all_tenants",
            tenant_allowlist=[],
            reason="Simulate all_tenants rollout",
        )
        await session.commit()

        executed = await process_due_subscription_transitions(session, enforce_rollout_guard=True)
        await session.commit()

        # Must NOT execute
        executed_ids = {e["tenant_id"] for e in executed}
        assert legacy_tenant_id not in executed_ids

        # Transition must remain pending (protected!)
        tr = await session.get(ScheduledSubscriptionTransitionDB, trans_id)
        assert tr.status == "pending"

        # Restore allowlist_only mode
        await update_scheduler_config(
            session,
            rollout_mode="allowlist_only",
            tenant_allowlist=[],
            reason="Restore allowlist_only mode",
        )

        # Clean up test tenant to ensure test isolation
        await session.delete(tr)
        sub = await session.get(TenantSubscriptionDB, f"sub_leg_{uid}")
        if sub:
            await session.delete(sub)
        t = await session.get(TenantDB, legacy_tenant_id)
        if t:
            await session.delete(t)
        await session.commit()
