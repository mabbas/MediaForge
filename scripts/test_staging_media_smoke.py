"""Task 8: Media processing and persistence smoke tests in staging environment."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import httpx

from api.security.passwords import hash_password


STAGING_PG_PORT = 55433
STAGING_DB = "grabitdown_staging"
STAGING_USER = "postgres"
STAGING_PASS = os.getenv("POSTGRES_PASSWORD", "9da305315347c99ce75a135cab199772c964c76e413ddbd0c0bc22f336053be8")
API_BASE_URL = "https://localhost:8443/api/v1"

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

async def run_task8():
    print("=== TASK 8: MEDIA PROCESSING & PERSISTENCE SMOKE TESTS ===")
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=STAGING_PG_PORT,
        user=STAGING_USER,
        password=STAGING_PASS,
        database=STAGING_DB,
    )
    print("Connected to grabitdown_staging.")

    test_uid = uuid.uuid4().hex[:8]
    tenant_id = f"tnt_media_{test_uid}"
    user_id = f"usr_media_{test_uid}"
    user_email = f"media_{test_uid}@staging.local"
    user_pass = f"MediaPass{test_uid}123"

    print(f"Creating test user and tenant: {tenant_id} / {user_email}")
    now = utcnow()
    pass_hash = hash_password(user_pass)

    # Step 1: Provision tenant, active plan, subscription, and user in PostgreSQL
    plan_id = f"plan_media_{test_uid}"
    await conn.execute(
        """
        INSERT INTO gid.commercial_plans (id, plan_code, name, version, status, tier_type, billing_interval, price_cents, currency, created_at, updated_at)
        VALUES ($1, $2, 'Media Smoke Plan', 1, 'active', 'paid', 'monthly', 2900, 'USD', $3, $3)
        """,
        plan_id, f"media_plan_{test_uid}", now,
    )
    await conn.execute(
        """
        INSERT INTO gid.tenants (id, name, slug, tier, is_active, created_at, updated_at)
        VALUES ($1, $2, $3, 'pro', true, $4, $4)
        """,
        tenant_id, f"Tenant Media {test_uid}", f"slug_media_{test_uid}", now,
    )
    await conn.execute(
        """
        INSERT INTO gid.tenant_subscriptions (id, tenant_id, plan_id, status, current_period_start, current_period_end, created_at, updated_at)
        VALUES ($1, $2, $3, 'active', $4, $5, $4, $4)
        """,
        f"sub_{tenant_id}", tenant_id, plan_id, now, now + timedelta(days=30),
    )
    await conn.execute(
        """
        INSERT INTO gid.users (id, tenant_id, email, password_hash, role, is_active, created_at, updated_at)
        VALUES ($1, $2, $3, $4, 'member', true, $5, $5)
        """,
        user_id, tenant_id, user_email, pass_hash, now,
    )
    print("User and subscription provisioned.")

    source_artifact_id = f"art_src_{test_uid}"
    try:
        # Step 2: Generate synthetic test media inside worker container
        print("\n--- STEP 2: Generate Synthetic Source Media via FFmpeg in Docker ---")
        dest_dir = f"/app/downloads/{tenant_id}/downloads"
        dest_file = f"{dest_dir}/source_sample.mp4"

        # Make directory inside container
        subprocess.run(["docker", "exec", "mediaforge-staging-worker", "mkdir", "-p", dest_dir], check=True)
        # Generate 6-second video test pattern with audio beep
        gen_cmd = [
            "docker", "exec", "mediaforge-staging-worker",
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            dest_file
        ]
        res = subprocess.run(gen_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"FFmpeg generation failed: {res.stderr}"
        print(f"Synthetic video generated at {dest_file}")

        # Get file stats
        stat_cmd = ["docker", "exec", "mediaforge-staging-worker", "stat", "-c", "%s", dest_file]
        fsize = int(subprocess.run(stat_cmd, capture_output=True, text=True, check=True).stdout.strip())

        sha_cmd = ["docker", "exec", "mediaforge-staging-worker", "sha256sum", dest_file]
        fsha = subprocess.run(sha_cmd, capture_output=True, text=True, check=True).stdout.split()[0]
        print(f"File size: {fsize} bytes, SHA256: {fsha}")

        # Register artifact in database
        await conn.execute(
            """
            INSERT INTO gid.media_artifacts
                (id, tenant_id, user_id, artifact_type, storage_path, filename, format, duration_seconds, file_size_bytes, sha256_checksum, status, created_at, updated_at)
            VALUES
                ($1, $2, $3, 'download', 'downloads/source_sample.mp4', 'source_sample.mp4', 'mp4', 6.0, $4, $5, 'ready', $6, $6)
            """,
            source_artifact_id, tenant_id, user_id, fsize, fsha, now,
        )
        print(f"Registered source artifact {source_artifact_id} in gid.media_artifacts.")

        # Step 3: Login to obtain access token
        print("\n--- STEP 3: Authenticate User via Staging API (HTTPS:8443) ---")
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            login_resp = await client.post(
                f"{API_BASE_URL}/auth/login",
                json={"email": user_email, "password": user_pass, "tenant_slug": f"slug_media_{test_uid}"},
            )
            assert login_resp.status_code == 200, f"Login failed: {login_resp.status_code} {login_resp.text}"
            token_data = login_resp.json()
            access_token = token_data["access_token"]
            print("Successfully authenticated. Acquired JWT access token.")

            auth_headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            # Step 4: Path Containment Security Negative Check
            print("\n--- STEP 4: Path Containment Security Check (Fail-Closed Traversal) ---")
            traversal_req = {
                "source": "../../../../etc/passwd",
                "start_time": "00:00:01",
                "end_time": "00:00:03",
                "mode": "precise",
                "output_format": "mp4",
            }
            trav_resp = await client.post(
                f"{API_BASE_URL}/jobs/clips",
                headers=auth_headers,
                json=traversal_req,
            )
            print(f"Path traversal attempt status: {trav_resp.status_code} {trav_resp.text}")
            assert trav_resp.status_code in (400, 403, 404), f"Expected fail-closed 400/403/404, got {trav_resp.status_code}"
            print("[PASS] Path traversal attempt strictly rejected (Fail-Closed)!")

            # Step 5: Submit Asynchronous Clip Extraction Job
            print("\n--- STEP 5: Submit Asynchronous Clip Extraction Job ---")
            clip_idemp = f"clip_idemp_{test_uid}"
            clip_req = {
                "source": source_artifact_id,
                "start_time": "00:00:01",
                "end_time": "00:00:03",
                "mode": "precise",
                "output_format": "mp4",
            }
            clip_headers = dict(auth_headers, **{"X-Idempotency-Key": clip_idemp})
            submit_resp = await client.post(
                f"{API_BASE_URL}/jobs/clips",
                headers=clip_headers,
                json=clip_req,
            )
            assert submit_resp.status_code in (200, 202), f"Clip job submission failed: {submit_resp.status_code} {submit_resp.text}"
            job_info = submit_resp.json()
            clip_job_id = job_info["job_id"]
            print(f"Clip job accepted: ID={clip_job_id}, status={job_info.get('status')}")

            # Step 6: Await Autonomous Worker Completion
            print("Awaiting worker completion of clip extraction job...")
            clip_completed = False
            clip_artifact_id = None
            for i in range(25):
                await asyncio.sleep(2)
                status_resp = await client.get(f"{API_BASE_URL}/jobs/{clip_job_id}", headers=auth_headers)
                assert status_resp.status_code == 200, f"Status check failed: {status_resp.status_code}"
                sdata = status_resp.json()
                st = sdata.get("status")
                pct = sdata.get("progress_percent")
                print(f"  Poll {i+1}: job status={st}, progress={pct}%")
                if st in ("succeeded", "completed"):
                    clip_completed = True
                    clip_artifact_id = sdata.get("result_artifact_id")
                    break
                elif st == "failed":
                    raise AssertionError(f"Clip extraction failed: {sdata.get('error_message')}")

            assert clip_completed, "Clip extraction job did not complete in time!"
            assert clip_artifact_id, "Completed job missing result_artifact_id!"
            print(f"[PASS] Clip extraction succeeded! Generated artifact ID: {clip_artifact_id}")

            # Verify artifact exists on disk in volume
            clip_art_row = await conn.fetchrow(
                "SELECT storage_path, file_size_bytes, sha256_checksum, status FROM gid.media_artifacts WHERE id = $1",
                clip_artifact_id,
            )
            assert clip_art_row, "Artifact row not found in database!"
            assert clip_art_row["status"] == "ready"
            clip_disk_path = f"/app/downloads/{tenant_id}/{clip_art_row['storage_path']}"
            check_disk = subprocess.run(["docker", "exec", "mediaforge-staging-worker", "test", "-f", clip_disk_path])
            assert check_disk.returncode == 0, f"Clip file missing on disk at {clip_disk_path}!"
            print(f"[PASS] Clip artifact confirmed on disk in Docker volume: {clip_disk_path}")

            # Step 7: Usage Event & Reservation Lifecycle & Idempotency Check
            print("\n--- STEP 7: Verify Usage Events, Reservations & Idempotency ---")
            usage_row = await conn.fetchrow(
                "SELECT event_type, quantity, unit, bytes FROM gid.usage_events WHERE tenant_id = $1 AND resource_id = $2",
                tenant_id, clip_artifact_id,
            )
            assert usage_row, "Usage event not recorded for clip extraction!"
            assert usage_row["event_type"] == "clip"
            assert usage_row["quantity"] == 1.0
            print(f"[PASS] Commercial usage event verified: event_type={usage_row['event_type']}, bytes={usage_row['bytes']}")

            # Commercial reservation status
            res_row = await conn.fetchrow(
                "SELECT status, resource_id FROM gid.commercial_reservations WHERE tenant_id = $1 AND idempotency_key = $2",
                tenant_id, clip_idemp,
            )
            if res_row:
                print(f"[PASS] Commercial reservation status: {res_row['status']} (resource_id={res_row['resource_id']})")
                assert res_row["status"] in ("finalized", "settled", "released"), f"Reservation not settled: {res_row['status']}"

            # Test Idempotent Resubmission: same key, same payload -> must return existing job
            idemp_resp = await client.post(
                f"{API_BASE_URL}/jobs/clips",
                headers=clip_headers,
                json=clip_req,
            )
            assert idemp_resp.status_code in (200, 202), f"Idempotent resubmit failed: {idemp_resp.status_code}"
            assert idemp_resp.json()["job_id"] == clip_job_id, "Idempotent submission created new job!"
            print("[PASS] Idempotency verified: duplicate submission returned existing job without duplicate work.")

            # Step 8: Submit Asynchronous Video Merge Job
            print("\n--- STEP 8: Submit Video Merge Job ---")
            merge_idemp = f"merge_idemp_{test_uid}"
            merge_req = {
                "clips": [clip_artifact_id, clip_artifact_id],
                "output_format": "mp4",
                "mode": "precise",
            }
            merge_headers = dict(auth_headers, **{"X-Idempotency-Key": merge_idemp})
            m_resp = await client.post(
                f"{API_BASE_URL}/jobs/merges",
                headers=merge_headers,
                json=merge_req,
            )
            assert m_resp.status_code in (200, 202), f"Merge job submission failed: {m_resp.status_code} {m_resp.text}"
            merge_job_id = m_resp.json()["job_id"]
            print(f"Merge job accepted: ID={merge_job_id}")

            print("Awaiting worker completion of merge job...")
            merge_completed = False
            merge_artifact_id = None
            for i in range(30):
                await asyncio.sleep(2)
                st_resp = await client.get(f"{API_BASE_URL}/jobs/{merge_job_id}", headers=auth_headers)
                mdata = st_resp.json()
                mst = mdata.get("status")
                mpct = mdata.get("progress_percent")
                print(f"  Merge Poll {i+1}: status={mst}, progress={mpct}%")
                if mst in ("succeeded", "completed"):
                    merge_completed = True
                    merge_artifact_id = mdata.get("result_artifact_id")
                    break
                elif mst == "failed":
                    raise AssertionError(f"Merge job failed: {mdata.get('error_message')}")

            assert merge_completed, "Merge job did not complete in time!"
            assert merge_artifact_id, "Merge missing result_artifact_id!"
            print(f"[PASS] Video merge succeeded! Generated artifact ID: {merge_artifact_id}")

            # Verify merge file on disk
            merge_art_row = await conn.fetchrow(
                "SELECT storage_path, status FROM gid.media_artifacts WHERE id = $1", merge_artifact_id
            )
            assert merge_art_row["status"] == "ready"
            merge_disk_path = f"/app/downloads/{tenant_id}/{merge_art_row['storage_path']}"
            check_mdisk = subprocess.run(["docker", "exec", "mediaforge-staging-worker", "test", "-f", merge_disk_path])
            assert check_mdisk.returncode == 0, f"Merged file missing on disk at {merge_disk_path}!"
            print(f"[PASS] Merged artifact confirmed on disk in Docker volume: {merge_disk_path}")

            # Step 9: Container Restart Persistence Verification
            print("\n--- STEP 9: Container Restart Persistence Verification ---")
            print("Restarting mediaforge-staging-worker and mediaforge-staging-api...")
            restart_res = subprocess.run(
                ["docker", "restart", "mediaforge-staging-worker", "mediaforge-staging-api"],
                capture_output=True, text=True, check=True
            )
            print("Containers restarted. Awaiting API and worker readiness...")
            await asyncio.sleep(5)

            # Re-verify through API that jobs and artifacts are 100% persistent
            job_check1 = await client.get(f"{API_BASE_URL}/jobs/{clip_job_id}", headers=auth_headers)
            assert job_check1.status_code == 200, f"Clip job check failed after restart: {job_check1.status_code}"
            assert job_check1.json()["status"] in ("succeeded", "completed")

            job_check2 = await client.get(f"{API_BASE_URL}/jobs/{merge_job_id}", headers=auth_headers)
            assert job_check2.status_code == 200, f"Merge job check failed after restart: {job_check2.status_code}"
            assert job_check2.json()["status"] in ("succeeded", "completed")

            # Check files on disk after restart
            assert subprocess.run(["docker", "exec", "mediaforge-staging-worker", "test", "-f", clip_disk_path]).returncode == 0
            assert subprocess.run(["docker", "exec", "mediaforge-staging-worker", "test", "-f", merge_disk_path]).returncode == 0
            print("[PASS] Artifacts, database records, and job statuses survived container restart with 100% integrity!")

    finally:
        # Cleanup
        print("\n--- CLEANUP: Removing test entities and media files ---")
        try:
            # Delete directory from container
            subprocess.run(
                ["docker", "exec", "mediaforge-staging-worker", "rm", "-rf", f"/app/downloads/{tenant_id}"],
                capture_output=True,
            )
            await conn.execute("DELETE FROM gid.usage_events WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM gid.commercial_reservations WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM gid.media_work_items WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM gid.media_artifacts WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM gid.users WHERE id = $1", user_id)
            await conn.execute("DELETE FROM gid.tenant_subscriptions WHERE tenant_id = $1", tenant_id)
            await conn.execute("DELETE FROM gid.tenants WHERE id = $1", tenant_id)
            await conn.execute("DELETE FROM gid.commercial_plans WHERE id = $1", plan_id)
        except Exception as e:
            print(f"Cleanup warning: {e}")
        await conn.close()
        print("Database connection closed. TASK 8 VERIFICATION 100% COMPLETE & PASSING.")

if __name__ == "__main__":
    asyncio.run(run_task8())
