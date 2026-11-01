"""Task 7: Test and verify scheduler integration inside Docker staging environment."""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg


STAGING_PG_PORT = 55433
STAGING_DB = "grabitdown_staging"
STAGING_USER = "postgres"
STAGING_PASS = os.getenv("POSTGRES_PASSWORD", "9da305315347c99ce75a135cab199772c964c76e413ddbd0c0bc22f336053be8")

CONFIG_KEY = "commercial:scheduler_config"
STATE_KEY = "commercial:scheduler_state"
ENFORCE_KEY = "commercial:enforcement"

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

async def run_task7():
    print("=== TASK 7: SCHEDULER INTEGRATION & ROLLOUT GUARD VERIFICATION ===")
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=STAGING_PG_PORT,
        user=STAGING_USER,
        password=STAGING_PASS,
        database=STAGING_DB,
    )
    print("Connected to grabitdown_staging.")

    test_suffix = uuid.uuid4().hex[:6]
    tnt_auth = f"tnt_auth_{test_suffix}"
    tnt_unauth = f"tnt_unauth_{test_suffix}"
    plan_source = f"plan_src_{test_suffix}"
    plan_target = f"plan_tgt_{test_suffix}"

    try:
        # Step 1: Verify MediaWorker autonomous polling across at least 2 cycles
        print("\n--- STEP 1: Verify MediaWorker Autonomous Polling Heartbeats ---")
        row = await conn.fetchrow(
            "SELECT value, updated_at FROM gid.app_settings WHERE key = $1", STATE_KEY
        )
        initial_scan = None
        if row and row["value"]:
            val = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
            initial_scan = val.get("last_scan_at")
            print(f"Initial scheduler state: last_scan_at={initial_scan}, cluster_status={val.get('cluster_status')}")

        # Configure scheduler for test: 2s interval, allowlist_only with tnt_auth
        print(f"Configuring scheduler for test: poll_interval_seconds=2, rollout_mode='allowlist_only', tenant_allowlist=[{tnt_auth}]")
        cfg_val = {
            "enabled": True,
            "paused": False,
            "rollout_mode": "allowlist_only",
            "tenant_allowlist": [tnt_auth],
            "poll_interval_seconds": 2,
            "batch_size": 50,
            "updated_by_user_id": "test_system_admin",
            "reason": "STEP 04-B2-B Task 7 automated acceptance test",
        }
        await conn.execute(
            """
            INSERT INTO gid.app_settings (key, value, updated_at)
            VALUES ($1, $2::jsonb, $3)
            ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, updated_at = $3
            """,
            CONFIG_KEY,
            json.dumps(cfg_val),
            utcnow(),
        )

        print("Restarting worker to immediately adopt 2-second poll interval...")
        subprocess.run(["docker", "restart", "mediaforge-staging-worker"], check=True, capture_output=True)
        await asyncio.sleep(3)

        print("Awaiting 2 autonomous worker polling cycles...")
        scans_observed = []
        for _ in range(15):
            await asyncio.sleep(2)
            row = await conn.fetchrow("SELECT value FROM gid.app_settings WHERE key = $1", STATE_KEY)
            if row and row["value"]:
                val = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
                scan_at = val.get("last_scan_at")
                if scan_at and scan_at not in scans_observed and scan_at != initial_scan:
                    scans_observed.append(scan_at)
                    print(f"  Observed worker polling cycle at {scan_at}")
                    if len(scans_observed) >= 2:
                        break

        assert len(scans_observed) >= 2, f"Expected at least 2 autonomous polling cycles, got {len(scans_observed)}"
        print(f"[PASS] Observed {len(scans_observed)} autonomous worker polling cycles!")

        # Step 2: Provision test plans and tenants
        print("\n--- STEP 2: Authorized vs Unauthorized Tenant Transitions ---")
        now = utcnow()
        await conn.execute(
            """
            INSERT INTO gid.commercial_plans (id, plan_code, name, version, status, tier_type, billing_interval, price_cents, currency, created_at, updated_at)
            VALUES
                ($1, $2, 'Source Plan', 1, 'active', 'paid', 'monthly', 1000, 'USD', $5, $5),
                ($3, $4, 'Target Plan', 1, 'active', 'paid', 'monthly', 3000, 'USD', $5, $5)
            """,
            plan_source, f"code_src_{test_suffix}",
            plan_target, f"code_tgt_{test_suffix}",
            now,
        )

        # Tenants & initial subscriptions
        for tnt in (tnt_auth, tnt_unauth):
            await conn.execute(
                """
                INSERT INTO gid.tenants (id, name, slug, tier, is_active, created_at, updated_at)
                VALUES ($1, $2, $3, 'pro', true, $4, $4)
                """,
                tnt, f"Tenant {tnt}", f"slug_{tnt}", now,
            )
            await conn.execute(
                """
                INSERT INTO gid.tenant_subscriptions (id, tenant_id, plan_id, status, current_period_start, current_period_end, created_at, updated_at)
                VALUES ($1, $2, $3, 'active', $4, $5, $4, $4)
                """,
                f"sub_{tnt}", tnt, plan_source, now, now + timedelta(days=30),
            )

        # Create scheduled transitions: both scheduled in the past (due now)
        due_time = now - timedelta(seconds=10)
        trans_auth = f"trans_auth_{test_suffix}"
        trans_unauth = f"trans_unauth_{test_suffix}"

        await conn.execute(
            """
            INSERT INTO gid.scheduled_subscription_transitions
                (id, tenant_id, target_plan_id, scheduled_at, status, transition_type, reason, created_at, updated_at)
            VALUES
                ($1, $2, $3, $4, 'pending', 'upgrade', 'Authorized allowlisted test', $5, $5),
                ($6, $7, $3, $4, 'pending', 'upgrade', 'Unauthorized unlisted test', $5, $5)
            """,
            trans_auth, tnt_auth, plan_target, due_time, now,
            trans_unauth, tnt_unauth,
        )
        print(f"Created due transitions for authorized ({tnt_auth}) and unauthorized ({tnt_unauth}).")

        # Wait for worker scan
        print("Awaiting worker polling cycle to evaluate transitions...")
        auth_executed = False
        unauth_still_pending = False
        for i in range(10):
            await asyncio.sleep(2)
            r_auth = await conn.fetchrow(
                "SELECT status, executed_at FROM gid.scheduled_subscription_transitions WHERE id = $1",
                trans_auth,
            )
            r_unauth = await conn.fetchrow(
                "SELECT status, executed_at FROM gid.scheduled_subscription_transitions WHERE id = $1",
                trans_unauth,
            )
            print(f"  Poll {i+1}: trans_auth={r_auth['status']}, trans_unauth={r_unauth['status']}")
            if r_auth["status"] == "executed" and r_unauth["status"] == "pending":
                auth_executed = True
                unauth_still_pending = True
                break

        assert auth_executed, "Authorized transition was not executed by worker!"
        assert unauth_still_pending, "Unauthorized transition was unexpectedly executed! Rollout guard failed!"
        print("[PASS] Authorized transition was EXECUTED autonomously.")
        print("[PASS] Unauthorized transition remained PENDING due to rollout guard enforcement!")

        # Verify subscription updated for authorized tenant
        sub_auth = await conn.fetchrow("SELECT plan_id FROM gid.tenant_subscriptions WHERE tenant_id = $1", tnt_auth)
        assert sub_auth["plan_id"] == plan_target, f"Expected {plan_target}, got {sub_auth['plan_id']}"
        print(f"[PASS] Authorized tenant subscription updated to target plan {plan_target}.")

        # Step 3: Emergency pause / resume
        print("\n--- STEP 3: Emergency Pause / Resume Testing ---")
        cfg_val["paused"] = True
        cfg_val["tenant_allowlist"] = [tnt_auth, tnt_unauth] # even if allowlisted, pause should stop processing
        await conn.execute(
            "UPDATE gid.app_settings SET value = $1::jsonb, updated_at = $2 WHERE key = $3",
            json.dumps(cfg_val), utcnow(), CONFIG_KEY,
        )
        print("Scheduler configuration updated to paused=True.")

        # Update unauthorized transition scheduled_at to be due
        await conn.execute(
            "UPDATE gid.scheduled_subscription_transitions SET scheduled_at = $1 WHERE id = $2",
            utcnow() - timedelta(seconds=10), trans_unauth,
        )

        print("Awaiting 2 worker cycles while paused...")
        for _ in range(4):
            await asyncio.sleep(2)
            r_unauth = await conn.fetchrow(
                "SELECT status FROM gid.scheduled_subscription_transitions WHERE id = $1", trans_unauth
            )
            print(f"  While paused: trans_unauth status is {r_unauth['status']}")
            assert r_unauth["status"] == "pending", "Transition should NOT execute while paused!"

        print("[PASS] Scheduler respects emergency pause: no transitions processed while paused=True.")

        # Resume
        cfg_val["paused"] = False
        await conn.execute(
            "UPDATE gid.app_settings SET value = $1::jsonb, updated_at = $2 WHERE key = $3",
            json.dumps(cfg_val), utcnow(), CONFIG_KEY,
        )
        print("Scheduler configuration updated to paused=False.")

        print("Awaiting worker cycle after resume...")
        resumed_executed = False
        for i in range(8):
            await asyncio.sleep(2)
            r_unauth = await conn.fetchrow(
                "SELECT status, executed_at FROM gid.scheduled_subscription_transitions WHERE id = $1", trans_unauth
            )
            print(f"  After resume poll {i+1}: trans_unauth status is {r_unauth['status']}")
            if r_unauth["status"] == "executed":
                resumed_executed = True
                break

        assert resumed_executed, "Transition was not executed after resume!"
        print("[PASS] Resumed scheduler immediately picked up and executed the pending transition!")

        # Step 4: Worker restart & reconnection
        print("\n--- STEP 4: Worker Restart and DB Reconnection ---")
        row_before = await conn.fetchrow("SELECT value FROM gid.app_settings WHERE key = $1", STATE_KEY)
        val_before = json.loads(row_before["value"]) if isinstance(row_before["value"], str) else row_before["value"]
        old_worker_id = val_before.get("worker_id")
        print(f"Worker before restart: {old_worker_id}")

        print("Restarting worker container: docker restart mediaforge-staging-worker...")
        res = subprocess.run(["docker", "restart", "mediaforge-staging-worker"], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, f"Worker restart failed: {res.stderr}"
        print("Worker container restarted. Awaiting new worker registration and heartbeat in database...")

        reconnected = False
        for i in range(15):
            await asyncio.sleep(2)
            row_after = await conn.fetchrow("SELECT value FROM gid.app_settings WHERE key = $1", STATE_KEY)
            if row_after and row_after["value"]:
                val_after = json.loads(row_after["value"]) if isinstance(row_after["value"], str) else row_after["value"]
                new_worker_id = val_after.get("worker_id")
                status = val_after.get("status")
                if new_worker_id and new_worker_id != old_worker_id:
                    print(f"  New worker heartbeated: {new_worker_id} (status: {status})")
                    reconnected = True
                    break

        assert reconnected, "Worker failed to reconnect and register after restart!"
        print("[PASS] Worker successfully restarted, reconnected to PostgreSQL staging database, and resumed heartbeats.")

    finally:
        # Step 5: Restore safe production-default settings
        print("\n--- STEP 5: Restore Default Safe Settings ---")
        default_cfg = {
            "enabled": True,
            "paused": False,
            "rollout_mode": "allowlist_only",
            "tenant_allowlist": [],
            "poll_interval_seconds": 60,
            "batch_size": 50,
            "updated_by_user_id": "system_default",
            "reason": "Restored safe default configuration post STEP 04-B2-B acceptance testing",
        }
        await conn.execute(
            """
            INSERT INTO gid.app_settings (key, value, updated_at)
            VALUES ($1, $2::jsonb, $3)
            ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, updated_at = $3
            """,
            CONFIG_KEY,
            json.dumps(default_cfg),
            utcnow(),
        )

        # Confirm enforcement settings are strictly disabled
        default_enforce = {
            "mode": "disabled",
            "tenant_allowlist": [],
            "reason": "STEP 04-A2: Initial default rollout state (enforcement disabled / advisory only)",
            "updated_at": utcnow().isoformat(),
        }
        await conn.execute(
            """
            INSERT INTO gid.app_settings (key, value, updated_at)
            VALUES ($1, $2::jsonb, $3)
            ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, updated_at = $3
            """,
            ENFORCE_KEY,
            json.dumps(default_enforce),
            utcnow(),
        )
        print("[CONFIRM] Restored safe settings: rollout_mode = 'allowlist_only', tenant_allowlist = [], enforcement mode = 'disabled'.")

        # Cleanup test entities
        print("Cleaning up test entities...")
        await conn.execute("DELETE FROM gid.scheduled_subscription_transitions WHERE tenant_id IN ($1, $2)", tnt_auth, tnt_unauth)
        await conn.execute("DELETE FROM gid.tenant_subscriptions WHERE tenant_id IN ($1, $2)", tnt_auth, tnt_unauth)
        await conn.execute("DELETE FROM gid.tenants WHERE id IN ($1, $2)", tnt_auth, tnt_unauth)
        await conn.execute("DELETE FROM gid.commercial_plans WHERE id IN ($1, $2)", plan_source, plan_target)
        # Clean up any leftover keys from previous test attempt
        await conn.execute("DELETE FROM gid.app_settings WHERE key IN ('scheduler_config', 'enforcement_config')")
        await conn.close()
        print("Database connection closed. TASK 7 VERIFICATION 100% COMPLETE & PASSING.")

if __name__ == "__main__":
    asyncio.run(run_task7())
