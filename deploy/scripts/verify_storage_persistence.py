"""MediaForge — Controlled Artifact Storage Consistency and Persistence Verification (STEP 02-E1-Fix-B).

Verifies:
1. Synthetic video generation inside container.
2. Real offline clip extraction through API.
3. Real video merging of authorized clips through API.
4. Persistent database records ('ready', duration, sha256, format).
5. File presence at database-referenced storage paths.
6. SHA-256 and FFprobe stream verification.
7. Reconciliation scan discovers files with zero missing ready files.
8. Persistence across API container restart.
9. Persistence across API container recreation (--force-recreate, volumes preserved).
10. Download integrity and byte/checksum matching after recreation.
11. Intra-tenant member isolation and cross-tenant anti-enumeration (404).
12. Controlled cleanup via API (DELETE), verifying DB status='deleted', disk unlinks,
    and zero dangling ready records.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.security.jwt import create_access_token

API_URL = "http://127.0.0.1:58000"
COMPOSE_DIR = str(REPO_ROOT / "deploy")
COMPOSE_FILE = str(REPO_ROOT / "deploy" / "docker-compose.yml")

# Dedicated Synthetic Test Tenants and Users
TENANT_ID = "00000000-0000-0000-0000-000000000091"
USER_OWNER = "00000000-0000-0000-0000-000000000092"
USER_MEM1 = "00000000-0000-0000-0000-000000000093"
USER_MEM2 = "00000000-0000-0000-0000-000000000094"

CROSS_TENANT_ID = "00000000-0000-0000-0000-000000000095"
CROSS_USER = "00000000-0000-0000-0000-000000000096"

DOWNLOAD_JOB_ID = "00000000-0000-0000-0000-000000000098"


def run_cmd(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run host command and return output."""
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed ({res.returncode}): {' '.join(cmd)}\nStdout: {res.stdout}\nStderr: {res.stderr}")
    return res


def docker_compose_exec(service: str, command: list[str], timeout: int = 60) -> str:
    """Execute command inside docker compose service."""
    full_cmd = [
        "docker", "compose",
        "--project-directory", COMPOSE_DIR,
        "-f", COMPOSE_FILE,
        "exec", "-T", service,
    ] + command
    return run_cmd(full_cmd, timeout=timeout).stdout


def api_request(
    method: str,
    path: str,
    token: str | None = None,
    json_data: dict | None = None,
) -> tuple[int, dict | bytes]:
    """Execute HTTP request to MediaForge API."""
    url = f"{API_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data_bytes = json.dumps(json_data).encode("utf-8") if json_data is not None else None
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()
            if "application/json" in content_type:
                return resp.status, json.loads(raw.decode("utf-8"))
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, {"detail": raw.decode("utf-8", errors="replace")}


def wait_for_api_healthy(max_retries: int = 20, delay_sec: float = 1.0) -> bool:
    """Poll health check endpoint until HTTP 200 is returned."""
    for _ in range(max_retries):
        try:
            code, data = api_request("GET", "/api/v1/health")
            if code == 200 and isinstance(data, dict) and data.get("status") == "healthy":
                return True
        except Exception:
            pass
        time.sleep(delay_sec)
    return False


def setup_database_fixtures():
    """Create test tenants, users, and download job record."""
    sql = f"""
    INSERT INTO gid.tenants (id, name, slug, tier, is_active, created_at, updated_at)
    VALUES
        ('{TENANT_ID}', 'Persistence Test Tenant', 'persist-tenant', 'enterprise', true, NOW(), NOW()),
        ('{CROSS_TENANT_ID}', 'Cross Tenant', 'cross-tenant', 'enterprise', true, NOW(), NOW())
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO gid.users (id, tenant_id, email, display_name, role, is_active, created_at, updated_at)
    VALUES
        ('{USER_OWNER}', '{TENANT_ID}', 'owner_persist@test.local', 'Persist Owner', 'owner', true, NOW(), NOW()),
        ('{USER_MEM1}', '{TENANT_ID}', 'mem1_persist@test.local', 'Persist Mem1', 'member', true, NOW(), NOW()),
        ('{USER_MEM2}', '{TENANT_ID}', 'mem2_persist@test.local', 'Persist Mem2', 'member', true, NOW(), NOW()),
        ('{CROSS_USER}', '{CROSS_TENANT_ID}', 'cross_persist@test.local', 'Cross User', 'member', true, NOW(), NOW())
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO gid.download_jobs (
        id, url, provider, media_type, status, priority, title,
        file_path, file_size_bytes, duration_seconds, user_id, tenant_id, created_at, updated_at
    ) VALUES (
        '{DOWNLOAD_JOB_ID}',
        'https://example.com/synthetic_persist_source.mp4',
        'direct',
        'video',
        'completed',
        'normal',
        'synthetic_persist_source.mp4',
        'downloads/synthetic_persist_source.mp4',
        150000,
        6,
        '{USER_MEM1}',
        '{TENANT_ID}',
        NOW(),
        NOW()
    ) ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id, file_path = EXCLUDED.file_path;
    """
    docker_compose_exec("postgres", ["psql", "-U", "postgres", "-d", "grabitdown", "-c", sql])


def teardown_database_fixtures():
    """Remove test database rows."""
    sql = f"""
    DELETE FROM gid.media_artifacts WHERE tenant_id = '{TENANT_ID}';
    DELETE FROM gid.download_jobs WHERE tenant_id = '{TENANT_ID}';
    DELETE FROM gid.users WHERE tenant_id IN ('{TENANT_ID}', '{CROSS_TENANT_ID}');
    DELETE FROM gid.tenants WHERE id IN ('{TENANT_ID}', '{CROSS_TENANT_ID}');
    """
    try:
        docker_compose_exec("postgres", ["psql", "-U", "postgres", "-d", "grabitdown", "-c", sql])
    except Exception as e:
        print(f"[WARN] Database teardown failed: {e}")


def main() -> int:
    print("=" * 75)
    print(" STEP 02-E1-Fix-B — Artifact Storage Consistency & Persistence Verification")
    print("=" * 75)

    # Generate JWT tokens
    token_owner, _ = create_access_token(USER_OWNER, TENANT_ID, extra_claims={"role": "owner"})
    token_mem1, _ = create_access_token(USER_MEM1, TENANT_ID, extra_claims={"role": "member"})
    token_mem2, _ = create_access_token(USER_MEM2, TENANT_ID, extra_claims={"role": "member"})
    token_cross, _ = create_access_token(CROSS_USER, CROSS_TENANT_ID, extra_claims={"role": "member"})

    try:
        # 1. Setup DB Fixtures
        print("\n[STEP 1] Setting up database fixtures (tenants, users, download job)...")
        setup_database_fixtures()
        print("[PASS] Database fixtures configured.")

        # 2. Generate Synthetic Media inside Container
        print("\n[STEP 2] Generating synthetic MP4 fixture in container storage...")
        tenant_dl_dir = f"/app/downloads/{TENANT_ID}/downloads"
        docker_compose_exec("api", ["mkdir", "-p", tenant_dl_dir])
        source_media_path = f"{tenant_dl_dir}/synthetic_persist_source.mp4"
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
            "-t", "6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            source_media_path,
        ]
        docker_compose_exec("api", ffmpeg_cmd)
        probe_source = docker_compose_exec("api", ["ffprobe", "-v", "quiet", "-show_format", "-of", "json", source_media_path])
        assert "synthetic_persist_source.mp4" in probe_source, "Synthetic source failed to probe"
        print(f"[PASS] Synthetic source media created at: {source_media_path}")

        # 3. Extract Clip 1
        print("\n[STEP 3] Extracting Clip 1 (00:00:01 to 00:00:03) via API...")
        s_c1, r_c1 = api_request(
            "POST",
            "/api/v1/clips/extract",
            token=token_mem1,
            json_data={
                "source": "downloads/synthetic_persist_source.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:03",
                "output_format": "mp4",
                "mode": "precise",
            },
        )
        assert s_c1 == 200, f"Clip 1 extraction failed: {r_c1}"
        clip1_id = r_c1["clip_id"]
        clip1_path = r_c1["output_path"]
        print(f"[PASS] Clip 1 extracted: id={clip1_id}, storage_path={clip1_path}")

        # 4. Extract Clip 2
        print("\n[STEP 4] Extracting Clip 2 (00:00:03 to 00:00:05) via API...")
        s_c2, r_c2 = api_request(
            "POST",
            "/api/v1/clips/extract",
            token=token_mem1,
            json_data={
                "source": "downloads/synthetic_persist_source.mp4",
                "start_time": "00:00:03",
                "end_time": "00:00:05",
                "output_format": "mp4",
                "mode": "precise",
            },
        )
        assert s_c2 == 200, f"Clip 2 extraction failed: {r_c2}"
        clip2_id = r_c2["clip_id"]
        clip2_path = r_c2["output_path"]
        print(f"[PASS] Clip 2 extracted: id={clip2_id}, storage_path={clip2_path}")

        # 5. Merge Clips 1 & 2
        print("\n[STEP 5] Merging Clip 1 and Clip 2 via API...")
        s_m, r_m = api_request(
            "POST",
            "/api/v1/clips/merge",
            token=token_mem1,
            json_data={
                "clips": [clip1_path, clip2_path],
                "output_format": "mp4",
            },
        )
        assert s_m == 200, f"Merge failed: {r_m}"
        merge_id = r_m["merge_id"]
        merge_path = r_m["output_path"]
        print(f"[PASS] Merged video created: id={merge_id}, storage_path={merge_path}")

        # 6. Verify Database Metadata for All 3 Artifacts
        print("\n[STEP 6] Verifying database records (status='ready', file_size, duration, sha256)...")
        for art_id, expected_type, exp_path in [
            (clip1_id, "clip", clip1_path),
            (clip2_id, "clip", clip2_path),
            (merge_id, "merge", merge_path),
        ]:
            s_art, art_data = api_request("GET", f"/api/v1/artifacts/{art_id}", token=token_mem1)
            assert s_art == 200, f"Failed to get artifact {art_id}: {art_data}"
            assert art_data["status"] == "ready", f"Artifact {art_id} status is {art_data['status']}, expected 'ready'"
            assert art_data["artifact_type"] == expected_type
            assert art_data["storage_path"] == exp_path
            assert art_data["file_size_bytes"] > 0
            assert art_data["duration_seconds"] > 0
            assert len(art_data["sha256_checksum"]) == 64
            print(f"[PASS] Artifact {art_id} ({expected_type}) DB record verified: size={art_data['file_size_bytes']}B, dur={art_data['duration_seconds']}s, sha256={art_data['sha256_checksum'][:16]}...")

        # 7. Verify Files on Disk and FFprobe
        print("\n[STEP 7] Verifying files exist at exact storage paths on disk in container...")
        for art_id, _, exp_path in [(clip1_id, "clip", clip1_path), (clip2_id, "clip", clip2_path), (merge_id, "merge", merge_path)]:
            disk_path = f"/app/downloads/{TENANT_ID}/{exp_path}"
            check_file = docker_compose_exec("api", ["test", "-f", disk_path])
            probe_out = docker_compose_exec("api", ["ffprobe", "-v", "quiet", "-show_format", "-of", "json", disk_path])
            probe_data = json.loads(probe_out)
            dur = float(probe_data["format"]["duration"])
            assert dur > 0.5, f"Suspicious duration {dur} for {disk_path}"
            print(f"[PASS] File exists on disk and is playable: {disk_path} (duration={dur:.2f}s)")

        # 8. Run Reconciliation Scanner
        print("\n[STEP 8] Running reconciliation audit filtered to test tenant...")
        recon_output = docker_compose_exec("api", ["python3", "-m", "api.services.reconcile_artifacts", "--tenant-id", TENANT_ID, "--json"])
        recon_data = json.loads(recon_output)
        print(f"[PASS] Reconciliation scanned: DB artifacts={recon_data['scanned_artifacts_count']}, files on disk={recon_data['scanned_files_count']}")
        assert recon_data["scanned_artifacts_count"] == 3, f"Expected 3 DB artifacts, got {recon_data['scanned_artifacts_count']}"
        assert recon_data["missing_ready_count"] == 0, f"Expected 0 missing ready files, got {recon_data['missing_ready_count']}"
        assert recon_data["stale_processing_count"] == 0, f"Expected 0 stale processing, got {recon_data['stale_processing_count']}"
        assert recon_data["orphaned_files_count"] == 0, f"Expected 0 orphaned files, got {recon_data['orphaned_files_count']}"
        assert len(recon_data["items"]) == 0, f"Reconciliation detected anomalies: {recon_data['items']}"
        print("[PASS] Tenant reconciliation completed with 100% consistency (ZERO anomalies).")

        # 9. Test Persistence Across Container Restart
        print("\n[STEP 9] Restarting API container (docker compose restart api)...")
        run_cmd(["docker", "compose", "--project-directory", COMPOSE_DIR, "-f", COMPOSE_FILE, "restart", "api"])
        assert wait_for_api_healthy(), "API container failed to recover after restart"
        print("[PASS] API container restarted and healthy.")

        for art_id in [clip1_id, clip2_id, merge_id]:
            s_art, art_data = api_request("GET", f"/api/v1/artifacts/{art_id}", token=token_mem1)
            assert s_art == 200 and art_data["status"] == "ready", f"Artifact {art_id} unavailable after restart"
        print("[PASS] All artifacts remain 'ready' and accessible after container restart.")

        # 10. Test Persistence Across Container Recreation (--force-recreate)
        print("\n[STEP 10] Recreating API container without deleting volumes (docker compose up -d --force-recreate api)...")
        run_cmd(["docker", "compose", "--project-directory", COMPOSE_DIR, "-f", COMPOSE_FILE, "up", "-d", "--force-recreate", "api"])
        assert wait_for_api_healthy(), "API container failed to recover after recreation"
        print("[PASS] API container recreated and healthy.")

        # 11. Verify Artifact Downloads and Integrity After Recreation
        print("\n[STEP 11] Verifying artifact downloads and SHA-256 integrity after container recreation...")
        for art_id, exp_path in [(clip1_id, clip1_path), (merge_id, merge_path)]:
            s_dl, content = api_request("GET", f"/api/v1/artifacts/{art_id}/download", token=token_mem1)
            assert s_dl == 200, f"Download failed for {art_id}: {s_dl}"
            assert isinstance(content, bytes), "Expected raw byte payload"
            sha = hashlib.sha256(content).hexdigest()

            # Cross-check with DB record
            _, meta = api_request("GET", f"/api/v1/artifacts/{art_id}", token=token_mem1)
            assert sha == meta["sha256_checksum"], f"Checksum mismatch for {art_id}!"
            assert len(content) == meta["file_size_bytes"], f"Byte size mismatch for {art_id}!"
            print(f"[PASS] Download verified for {art_id}: size={len(content)} bytes, sha256={sha[:16]}... (matches DB record)")

        # 12. Verify Intra-Tenant and Cross-Tenant Isolation
        print("\n[STEP 12] Verifying intra-tenant member and cross-tenant isolation...")
        # Member 2 (same tenant, not creator) cannot download or see Member 1's clip (404)
        s_m2_clip, _ = api_request("GET", f"/api/v1/artifacts/{clip1_id}/download", token=token_mem2)
        assert s_m2_clip == 404, f"Member 2 received {s_m2_clip} instead of 404 for clip"
        s_m2_mrg, _ = api_request("GET", f"/api/v1/artifacts/{merge_id}/download", token=token_mem2)
        assert s_m2_mrg == 404, f"Member 2 received {s_m2_mrg} instead of 404 for merge"
        print("[PASS] Same-tenant Member 2 correctly rejected with HTTP 404 (anti-enumeration).")

        # Tenant Owner can download both
        s_own_clip, _ = api_request("GET", f"/api/v1/artifacts/{clip1_id}/download", token=token_owner)
        assert s_own_clip == 200, f"Tenant owner failed to download clip: {s_own_clip}"
        s_own_mrg, _ = api_request("GET", f"/api/v1/artifacts/{merge_id}/download", token=token_owner)
        assert s_own_mrg == 200, f"Tenant owner failed to download merge: {s_own_mrg}"
        print("[PASS] Tenant Owner successfully authorized for member artifacts (HTTP 200).")

        # Cross Tenant User cannot download (404)
        s_cross_clip, _ = api_request("GET", f"/api/v1/artifacts/{clip1_id}/download", token=token_cross)
        assert s_cross_clip == 404, f"Cross-tenant user received {s_cross_clip} instead of 404"
        print("[PASS] Cross-tenant caller rejected with HTTP 404.")

        # 13. Controlled Cleanup via API
        print("\n[STEP 13] Executing controlled cleanup via API DELETE endpoints...")
        for art_id, exp_path in [(clip1_id, clip1_path), (clip2_id, clip2_path), (merge_id, merge_path)]:
            s_del, r_del = api_request("DELETE", f"/api/v1/artifacts/{art_id}", token=token_mem1)
            assert s_del == 200, f"DELETE failed for {art_id}: {r_del}"
            assert r_del["success"] is True

            # Verify API returns 404 for deleted artifact
            s_chk, chk_data = api_request("GET", f"/api/v1/artifacts/{art_id}", token=token_owner)
            assert s_chk == 404, f"Expected 404 after deletion, got {s_chk}"

            # Verify DB status is 'deleted'
            db_status = docker_compose_exec("postgres", ["psql", "-U", "postgres", "-d", "grabitdown", "-t", "-A", "-c", f"SELECT status FROM gid.media_artifacts WHERE id = '{art_id}';"]).strip()
            assert db_status == "deleted", f"DB status is '{db_status}', expected 'deleted'"

            # Verify file on disk is unlinked
            disk_p = f"/app/downloads/{TENANT_ID}/{exp_path}"
            res_test = run_cmd([
                "docker", "compose", "--project-directory", COMPOSE_DIR, "-f", COMPOSE_FILE,
                "exec", "-T", "api", "sh", "-c", f"test -f '{disk_p}' && echo 'EXISTS' || echo 'NOT_FOUND'"
            ])
            assert "NOT_FOUND" in res_test.stdout, f"File {disk_p} was not unlinked on DELETE!"
            print(f"[PASS] Artifact {art_id} deleted cleanly: API returns 404, DB status='deleted', disk file unlinked.")

        # Remove source media from disk
        docker_compose_exec("api", ["rm", "-f", source_media_path])
        print("[PASS] Synthetic source media unlinked.")

        print("\n" + "=" * 75)
        print("[SUCCESS] ALL PERSISTENCE AND CONSISTENCY CHECKS PASSED (13/13)")
        print("=" * 75)
        return 0

    finally:
        print("\nCleaning up database test fixtures...")
        teardown_database_fixtures()
        # Clean tenant directory inside container if empty
        try:
            docker_compose_exec("api", ["rm", "-rf", f"/app/downloads/{TENANT_ID}", f"/app/downloads/{CROSS_TENANT_ID}"])
        except Exception:
            pass
        print("Teardown complete.")


if __name__ == "__main__":
    sys.exit(main())
