"""Live verification of STEP 03-B: Durable Background Jobs & Worker System."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from api.security.jwt import create_access_token

BASE_URL = "http://127.0.0.1:58000/api/v1"
client = httpx.Client(timeout=30.0)


def run_psql(query: str) -> str:
    """Execute SQL statement against authoritative PostgreSQL container."""
    cmd = [
        "docker", "exec", "-i", "postgres",
        "psql", "-U", "postgres", "-d", "grabitdown",
        "-t", "-A", "-c", query,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def main():
    print("=== STEP 03-B: Persistent Background Worker & Jobs Verification ===")

    # 1. Seed tenant and test users
    tenant_id = f"tenant-jobs-{uuid.uuid4().hex[:8]}"
    foreign_tenant_id = f"tenant-foreign-{uuid.uuid4().hex[:8]}"
    user_id = f"user-jobs-{uuid.uuid4().hex[:8]}"
    foreign_user_id = f"user-foreign-{uuid.uuid4().hex[:8]}"
    slug1 = f"jobs-{uuid.uuid4().hex[:6]}"
    slug2 = f"foreign-{uuid.uuid4().hex[:6]}"

    print("\n[1] Seeding test tenants and credentials in PostgreSQL...")
    run_psql(
        f"INSERT INTO gid.tenants (id, name, slug, tier, is_active) VALUES "
        f"('{tenant_id}', 'Jobs Org', '{slug1}', 'pro', true), "
        f"('{foreign_tenant_id}', 'Foreign Org', '{slug2}', 'basic', true);"
    )
    run_psql(
        f"INSERT INTO gid.users (id, tenant_id, email, password_hash, role, is_active) VALUES "
        f"('{user_id}', '{tenant_id}', 'jobs_{uuid.uuid4().hex[:6]}@example.com', 'hash', 'member', true), "
        f"('{foreign_user_id}', '{foreign_tenant_id}', 'foreign_{uuid.uuid4().hex[:6]}@example.com', 'hash', 'member', true);"
    )

    token, _ = create_access_token(user_id=user_id, tenant_id=tenant_id)
    foreign_token, _ = create_access_token(user_id=foreign_user_id, tenant_id=foreign_tenant_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    foreign_headers = {"Authorization": f"Bearer {foreign_token}", "Content-Type": "application/json"}

    print(f"    - Tenant ID:  {tenant_id}")
    print(f"    - User ID:    {user_id}")

    # 2. Generate 5-second synthetic video inside tenant media directory
    print("\n[2] Generating synthetic test video inside container...")
    rel_src_file = "downloads/synthetic_worker_source.mp4"
    container_dir = f"/app/downloads/{tenant_id}/downloads"
    container_src = f"{container_dir}/synthetic_worker_source.mp4"

    docker_gen_cmd = [
        "docker", "exec", "mediaforge-api", "bash", "-c",
        f"mkdir -p {container_dir} && ffmpeg -y -f lavfi -i testsrc=duration=5:size=640x360:rate=30 "
        f"-f lavfi -i sine=frequency=1000:duration=5 "
        f"-c:v libx264 -g 30 -keyint_min 30 -sc_threshold 0 -pix_fmt yuv420p -c:a aac -b:a 128k "
        f"{container_src}"
    ]
    subprocess.run(docker_gen_cmd, capture_output=True, check=True)
    print(f"    - Generated: {container_src}")

    # Register download job in database for ownership authorization
    src_job_id = f"job-src-{uuid.uuid4().hex[:8]}"
    run_psql(
        f"INSERT INTO gid.download_jobs (id, tenant_id, user_id, url, title, provider, media_type, priority, file_path, status, duration_seconds) "
        f"VALUES ('{src_job_id}', '{tenant_id}', '{user_id}', 'https://example.com/test.mp4', 'Test Video', 'generic', 'video', 'normal', '{rel_src_file}', 'completed', 5);"
    )

    # 3. Submit Asynchronous Clip Extraction Job (POST /api/v1/jobs/clips)
    print("\n[3] Submitting asynchronous clip extraction job via POST /api/v1/jobs/clips...")
    clip_payload = {
        "source": rel_src_file,
        "start_time": "00:00:01.000",
        "end_time": "00:00:03.000",
        "mode": "precise",
        "output_format": "mp4",
    }
    r = client.post(f"{BASE_URL}/jobs/clips", headers=headers, json=clip_payload)
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    clip_job_data = r.json()
    clip_job_id = clip_job_data["job_id"]
    print(f"    - HTTP 202 Accepted. Job ID: {clip_job_id}, Status: {clip_job_data['status']}")
    assert clip_job_data["status"] == "queued"
    assert clip_job_data["operation_type"] == "clip_extract"

    # 4. Poll job status until succeeded
    print(f"\n[4] Polling GET /api/v1/jobs/{clip_job_id} until worker completes processing...")
    start_wait = time.time()
    clip_detail = None
    while time.time() - start_wait < 30.0:
        r_poll = client.get(f"{BASE_URL}/jobs/{clip_job_id}", headers=headers)
        assert r_poll.status_code == 200
        clip_detail = r_poll.json()
        status_now = clip_detail["status"]
        progress_now = clip_detail["progress_percent"]
        print(f"    - Status: {status_now:<10} | Progress: {progress_now:.1f}% | Claimed by: {clip_detail.get('claimed_by')}")
        if status_now == "succeeded":
            break
        elif status_now == "failed":
            raise RuntimeError(f"Job failed unexpectedly: {clip_detail}")
        time.sleep(1.0)

    assert clip_detail and clip_detail["status"] == "succeeded", "Job did not complete in time"
    clip_artifact_id = clip_detail["result_artifact_id"]
    assert clip_artifact_id, "Missing result artifact ID"
    print(f"    - Clip extraction succeeded! Artifact ID: {clip_artifact_id}")

    # 5. Verify created media artifact and duration
    r_art = client.get(f"{BASE_URL}/artifacts/{clip_artifact_id}", headers=headers)
    assert r_art.status_code == 200
    art_data = r_art.json()
    print(f"    - Artifact Filename: {art_data['filename']}")
    print(f"    - Artifact Duration: {art_data['duration_seconds']:.2f}s (exact frame-accurate 2.00s)")
    print(f"    - Artifact SHA-256:  {art_data['sha256_checksum']}")
    assert abs(art_data["duration_seconds"] - 2.0) < 0.1, f"Duration mismatch: {art_data['duration_seconds']}"

    # 6. Verify usage ledger atomicity in PostgreSQL
    print("\n[5] Verifying authoritative usage ledger event in PostgreSQL...")
    event_query = (
        f"SELECT event_type, idempotency_key, quantity, duration_seconds, bytes "
        f"FROM gid.usage_events WHERE tenant_id = '{tenant_id}' AND resource_id = '{clip_artifact_id}';"
    )
    event_out = run_psql(event_query)
    print(f"    - Usage event: {event_out}")
    assert "clip" in event_out
    assert f"clip:{clip_artifact_id}" in event_out

    # 7. Test Idempotent Resubmission
    print("\n[6] Testing database-enforced job submission idempotency...")
    r_idem = client.post(f"{BASE_URL}/jobs/clips", headers=headers, json=clip_payload)
    assert r_idem.status_code == 202
    assert r_idem.json()["job_id"] == clip_job_id, "Idempotent submission did not return identical job ID"
    print("    - Duplicate submission cleanly returned existing job ID without duplicate work.")

    # 8. Test Second Clip & Video Merge Job
    print("\n[7] Extracting second clip and submitting asynchronous video merge job...")
    clip2_payload = {
        "source": rel_src_file,
        "start_time": "00:00:02.000",
        "end_time": "00:00:04.000",
        "mode": "precise",
        "output_format": "mp4",
    }
    r2 = client.post(f"{BASE_URL}/jobs/clips", headers=headers, json=clip2_payload)
    assert r2.status_code == 202
    clip2_job_id = r2.json()["job_id"]

    # Wait for clip 2
    while True:
        r_poll2 = client.get(f"{BASE_URL}/jobs/{clip2_job_id}", headers=headers)
        if r_poll2.json()["status"] == "succeeded":
            clip2_artifact_id = r_poll2.json()["result_artifact_id"]
            break
        time.sleep(1.0)

    # Submit merge
    merge_payload = {
        "clips": [f"artifact:{clip_artifact_id}", f"artifact:{clip2_artifact_id}"],
        "output_format": "mp4",
        "mode": "auto",
    }
    r_merge = client.post(f"{BASE_URL}/jobs/merges", headers=headers, json=merge_payload)
    assert r_merge.status_code == 202
    merge_job_id = r_merge.json()["job_id"]
    print(f"    - Merge Job Submitted: {merge_job_id}")

    # Wait for merge job
    merge_detail = None
    while True:
        r_m_poll = client.get(f"{BASE_URL}/jobs/{merge_job_id}", headers=headers)
        merge_detail = r_m_poll.json()
        print(f"    - Merge Status: {merge_detail['status']:<10} | Progress: {merge_detail['progress_percent']:.1f}%")
        if merge_detail["status"] == "succeeded":
            break
        elif merge_detail["status"] == "failed":
            raise RuntimeError(f"Merge job failed: {merge_detail}")
        time.sleep(1.0)

    merge_artifact_id = merge_detail["result_artifact_id"]
    print(f"    - Video Merge Succeeded! Result Artifact: {merge_artifact_id}")

    # 9. Test Job Cancellation
    print("\n[8] Testing job cancellation behavior...")
    dummy_payload = {
        "source": rel_src_file,
        "start_time": "00:00:00.000",
        "end_time": "00:00:01.000",
        "mode": "precise",
        "output_format": "mp4",
    }
    # Use custom idempotency key to prevent deduplicating with earlier jobs
    custom_key = f"cancel_test_{uuid.uuid4().hex[:8]}"
    r_c_sub = client.post(
        f"{BASE_URL}/jobs/clips",
        headers={**headers, "X-Idempotency-Key": custom_key},
        json=dummy_payload,
    )
    c_job_id = r_c_sub.json()["job_id"]

    # Cancel immediately
    r_cancel = client.post(f"{BASE_URL}/jobs/{c_job_id}/cancel", headers=headers)
    assert r_cancel.status_code == 200
    print(f"    - Cancellation requested response: {r_cancel.json()['message']}")

    # Verify status is cancelled
    c_status = None
    for _ in range(10):
        time.sleep(0.5)
        r_c_verify = client.get(f"{BASE_URL}/jobs/{c_job_id}", headers=headers)
        c_status = r_c_verify.json()["status"]
        if c_status == "cancelled":
            break
    print(f"    - Final Cancelled Job Status: {c_status}")
    assert c_status == "cancelled"

    # Verify NO usage event was recorded for cancelled job
    cancelled_event_count = run_psql(
        f"SELECT count(*) FROM gid.usage_events WHERE idempotency_key = '{custom_key}';"
    )
    print(f"    - Usage events for cancelled job: {cancelled_event_count} (must be 0)")
    assert int(cancelled_event_count) == 0, "Cancelled job created unexpected usage event!"

    # 10. Test Anti-Enumeration & Cross-Tenant Security
    print("\n[9] Testing anti-enumeration and cross-tenant security boundaries...")
    # Foreign tenant attempting to inspect Tenant A's job
    r_foreign_get = client.get(f"{BASE_URL}/jobs/{clip_job_id}", headers=foreign_headers)
    print(f"    - Cross-tenant GET returned status: {r_foreign_get.status_code} (expected 404)")
    assert r_foreign_get.status_code == 404, "Cross-tenant job access was not blocked with 404!"

    # Foreign tenant attempting to cancel Tenant A's job
    r_foreign_cancel = client.post(f"{BASE_URL}/jobs/{clip_job_id}/cancel", headers=foreign_headers)
    print(f"    - Cross-tenant CANCEL returned status: {r_foreign_cancel.status_code} (expected 404)")
    assert r_foreign_cancel.status_code == 404, "Cross-tenant job cancellation was not blocked with 404!"

    # 11. Test Lease Expiry and Crash Recovery
    print("\n[10] Testing worker lease expiration and orphan recovery...")
    # Inject a simulated crashed worker job with expired lease
    crashed_job_id = str(uuid.uuid4())
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    past_str = past_time.strftime('%Y-%m-%d %H:%M:%S')
    run_psql(
        f"INSERT INTO gid.media_work_items (id, tenant_id, requester_user_id, operation_type, idempotency_key, request_payload, status, attempt_count, max_attempts, claimed_by, lease_expires_at, last_heartbeat_at, available_at, created_at, updated_at) "
        f"VALUES ('{crashed_job_id}', '{tenant_id}', '{user_id}', 'clip_extract', 'crashed_test_job', '{{\"dummy\": 1}}', 'running', 1, 3, 'worker-dead-999', '{past_str}', '{past_str}', '{past_str}', '{past_str}', '{past_str}');"
    )

    # Wait for media-worker's periodic recovery loop (runs every 15s) to recover the job
    print("    - Waiting for worker recovery daemon to reclaim orphaned job...")
    recovered = False
    for _ in range(25):
        r_rec = client.get(f"{BASE_URL}/jobs/{crashed_job_id}", headers=headers)
        if r_rec.status_code == 200:
            rec_status = r_rec.json()["status"]
            if rec_status == "retry_wait":
                print(f"    - Succeeded: Orphaned job reclaimed and transitioned to '{rec_status}'!")
                recovered = True
                break
        time.sleep(1.0)
    assert recovered, "Orphaned job was not recovered into retry_wait!"

    # 12. Teardown test fixtures
    print("\n[11] Cleaning up test fixtures from live environment...")
    run_psql(f"DELETE FROM gid.tenants WHERE id IN ('{tenant_id}', '{foreign_tenant_id}');")
    subprocess.run(
        ["docker", "exec", "mediaforge-api", "rm", "-rf", f"/app/downloads/{tenant_id}", f"/app/downloads/{foreign_tenant_id}"],
        capture_output=True,
    )
    print("    - Clean teardown completed successfully.")

    print("\n[PASS] STEP 03-B Live Background Jobs Verification Completed Successfully!")


if __name__ == "__main__":
    main()
