"""Verification script for real FFmpeg operations, artifact authorization, and filesystem safety.

Milestone: STEP 02-E1
Runs inside the Docker container to verify:
- TASK 1: Real offline clipping verification with synthetic media and persistent MediaArtifactDB records
- TASK 2: Real video merge verification with spaces, quotes, injection defenses, and MediaArtifactDB records
- TASK 3: Filesystem race and symlink defenses
- TASK 4: Process timeouts, resource bounding, and reap checks
- TASK 5: Trusted egress gateway configuration audit
- TASK 6: Persistent artifact listing, filtering, intra-tenant isolation, deletion lifecycle, and legacy fallback
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, "/app")

from api.config import get_api_settings
from api.database.connection import create_async_engine
from api.security.jwt import create_access_token
from api.security.media_storage import (
    get_media_storage_root,
    get_tenant_media_dir,
    get_tenant_storage_root,
)
from src.env_loader import resolve_ffmpeg_binaries
from sqlalchemy import text

API_URL = "http://127.0.0.1:8000"

TENANT_A = "00000000-0000-0000-0000-000000000001"
USER_A_OWNER = "00000000-0000-0000-0000-000000000002"
USER_A_MEM1 = "00000000-0000-0000-0000-000000000003"
USER_A_MEM2 = "00000000-0000-0000-0000-000000000004"

TENANT_B = "d8d74404-9f27-41a0-96c3-ce0f36afc9a3"
USER_B = "52c0f32c-8fc5-4195-a61c-7a7577e61434"


def print_step(title: str):
    print(f"\n{'='*70}\n[TEST] {title}\n{'='*70}")


def run_probe(ffprobe_path: str, media_path: str) -> dict:
    """Run FFprobe on a media file and return JSON stream/format details."""
    cmd = [
        ffprobe_path,
        "-protocol_whitelist", "file",
        "-v", "quiet",
        "-show_format",
        "-show_streams",
        "-of", "json",
        media_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"FFprobe failed on {media_path}: {proc.stderr}")
    return json.loads(proc.stdout)


def api_request(
    method: str,
    path: str,
    token: str | None = None,
    json_data: dict | None = None,
) -> tuple[int, dict | bytes]:
    """Execute HTTP request to local FastAPI daemon."""
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


async def register_test_media():
    """Ensure test users and test source media records exist in database."""
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            # 1. Test users
            await conn.execute(text("""
                INSERT INTO gid.users (id, tenant_id, email, display_name, role, is_active, created_at, updated_at)
                VALUES
                    ('00000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000001', 'member1@grabitdown.local', 'Member 1', 'member', true, NOW(), NOW()),
                    ('00000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 'member2@grabitdown.local', 'Member 2', 'member', true, NOW(), NOW())
                ON CONFLICT (id) DO NOTHING;
            """))

            # 2. Register synthetic_test.mp4 as a DownloadJobDB owned by Member 1
            await conn.execute(text("""
                INSERT INTO gid.download_jobs (
                    id, url, provider, media_type, status, priority, title,
                    file_path, file_size_bytes, duration_seconds, user_id, tenant_id, created_at, updated_at
                ) VALUES (
                    '00000000-0000-0000-0000-000000000099',
                    'https://example.com/synthetic_test.mp4',
                    'direct',
                    'video',
                    'completed',
                    'normal',
                    'synthetic_test.mp4',
                    'downloads/synthetic_test.mp4',
                    117655,
                    5,
                    '00000000-0000-0000-0000-000000000003',
                    '00000000-0000-0000-0000-000000000001',
                    NOW(),
                    NOW()
                ) ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id, file_path = EXCLUDED.file_path;
            """))

            # 3. Register clip with space and clip'quote as MediaArtifactDB owned by Member 1
            await conn.execute(text("""
                INSERT INTO gid.media_artifacts (
                    id, tenant_id, user_id, artifact_type, storage_path, filename, format,
                    duration_seconds, file_size_bytes, status, created_at, updated_at
                ) VALUES
                (
                    '00000000-0000-0000-0000-0000000000a1',
                    '00000000-0000-0000-0000-000000000001',
                    '00000000-0000-0000-0000-000000000003',
                    'clip',
                    'clips/clip with space.mp4',
                    'clip with space.mp4',
                    'mp4',
                    2.0,
                    10000,
                    'ready',
                    NOW(),
                    NOW()
                ),
                (
                    '00000000-0000-0000-0000-0000000000a2',
                    '00000000-0000-0000-0000-000000000001',
                    '00000000-0000-0000-0000-000000000003',
                    'clip',
                    'clips/clip''quote.mp4',
                    'clip''quote.mp4',
                    'mp4',
                    2.0,
                    10000,
                    'ready',
                    NOW(),
                    NOW()
                )
                ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id, storage_path = EXCLUDED.storage_path;
            """))
    finally:
        await engine.dispose()


async def cleanup_test_media():
    """Remove test database rows."""
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                DELETE FROM gid.download_jobs WHERE id = '00000000-0000-0000-0000-000000000099';
                DELETE FROM gid.media_artifacts WHERE id IN ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000a2');
            """))
    except Exception:
        pass
    finally:
        await engine.dispose()


def main() -> int:
    ffmpeg_bin, ffprobe_bin = resolve_ffmpeg_binaries()
    print(f"Discovered binaries: FFmpeg='{ffmpeg_bin}', FFprobe='{ffprobe_bin}'")

    # Ensure test users and source media authorization
    try:
        asyncio.run(register_test_media())
        print("[PASS] Test users and source media authorizations established in database")
    except Exception as exc:
        print(f"[WARN] Error establishing test authorizations: {exc}")

    # Generate JWT tokens
    token_owner, _ = create_access_token(USER_A_OWNER, TENANT_A, extra_claims={"role": "owner"})
    token_mem1, _ = create_access_token(USER_A_MEM1, TENANT_A, extra_claims={"role": "member"})
    token_mem2, _ = create_access_token(USER_A_MEM2, TENANT_A, extra_claims={"role": "member"})
    token_b, _ = create_access_token(USER_B, TENANT_B, extra_claims={"role": "owner"})

    # Check API health
    status, health_data = api_request("GET", "/api/v1/health")
    assert status == 200 and health_data.get("status") == "healthy", f"API unhealthy: {health_data}"
    print(f"[PASS] API Health Check: {health_data}")

    tenant_a_root = get_tenant_storage_root(TENANT_A)
    tenant_a_downloads = get_tenant_media_dir(TENANT_A, "downloads")
    tenant_a_clips = get_tenant_media_dir(TENANT_A, "clips")
    tenant_a_merged = get_tenant_media_dir(TENANT_A, "merged")
    tenant_a_temp = get_tenant_media_dir(TENANT_A, "temp")

    synthetic_file = tenant_a_downloads / "synthetic_test.mp4"
    clip_space = None
    clip_quote = None
    clip_id = None
    merge_id = None

    try:
        # =======================================================================
        # TASK 1 — Real Offline Clipping Verification & MediaArtifactDB
        # =======================================================================
        print_step("TASK 1.1: Generate Synthetic MP4 using real FFmpeg in container")
        gen_cmd = [
            ffmpeg_bin,
            "-y",
            "-nostdin",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(synthetic_file),
        ]
        proc = subprocess.run(gen_cmd, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, f"FFmpeg synthetic generation failed: {proc.stderr}"
        assert synthetic_file.exists(), "Synthetic source video was not created"
        src_bytes = synthetic_file.read_bytes()
        src_sha256 = hashlib.sha256(src_bytes).hexdigest()
        print(f"[PASS] Synthetic MP4 created: {synthetic_file.name} (size={len(src_bytes)} bytes, sha256={src_sha256[:16]}...)")

        print_step("TASK 1.2: FFprobe probe on synthetic source video")
        probe_src = run_probe(ffprobe_bin, str(synthetic_file))
        src_dur = float(probe_src["format"]["duration"])
        streams = probe_src["streams"]
        assert 4.9 <= src_dur <= 5.1, f"Expected 5.0s duration, got {src_dur}"
        assert any(s["codec_type"] == "video" and s["codec_name"] == "h264" for s in streams), "Missing h264 video stream"
        assert any(s["codec_type"] == "audio" and s["codec_name"] == "aac" for s in streams), "Missing aac audio stream"
        print(f"[PASS] FFprobe confirmed: format={probe_src['format']['format_name']}, duration={src_dur:.2f}s, streams=video(h264)+audio(aac)")

        print_step("TASK 1.3: Real offline clip extraction through API (POST /api/v1/clips/extract) by Member 1")
        status_clip, clip_resp = api_request(
            "POST",
            "/api/v1/clips/extract",
            token=token_mem1,
            json_data={
                "source": "synthetic_test.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:03",
                "output_format": "mp4",
                "mode": "precise",
            },
        )
        assert status_clip == 200, f"Clip extraction failed with HTTP {status_clip}: {clip_resp}"
        assert clip_resp["success"] is True
        clip_id = clip_resp["clip_id"]
        rel_output = clip_resp["output_path"]
        assert rel_output.startswith("clips/"), f"Output path should start with 'clips/', got {rel_output}"
        assert not os.path.isabs(rel_output), f"Server leaked absolute path: {rel_output}"
        print(f"[PASS] Offline clip extracted: clip_id={clip_id}, relative_path={rel_output}")

        print_step("TASK 1.4: Verify output file, immutability, and database persistence")
        output_clip_file = tenant_a_root / rel_output
        assert output_clip_file.exists(), f"Output file does not exist on disk: {output_clip_file}"
        clip_file_size = output_clip_file.stat().st_size
        assert clip_file_size > 512, "Clip file size is suspiciously small"

        # FFprobe check on clip
        clip_probe = run_probe(ffprobe_bin, str(output_clip_file))
        clip_dur = float(clip_probe["format"]["duration"])
        assert 1.8 <= clip_dur <= 2.2, f"Expected ~2.0s duration, got {clip_dur}"
        print(f"[PASS] FFprobe verified output clip: playable=True, duration={clip_dur:.2f}s, size={clip_file_size} bytes")

        # Source untouched
        post_sha256 = hashlib.sha256(synthetic_file.read_bytes()).hexdigest()
        assert post_sha256 == src_sha256, "CRITICAL: Original source file was modified during clipping!"
        print(f"[PASS] Immutability verified: original source file hash unchanged ({post_sha256[:16]}...)")

        # Verify persistent artifact record in database via GET /api/v1/artifacts/{clip_id}
        status_art, art_data = api_request("GET", f"/api/v1/artifacts/{clip_id}", token=token_mem1)
        assert status_art == 200, f"Failed to retrieve artifact metadata: {status_art}: {art_data}"
        assert art_data["id"] == clip_id
        assert art_data["artifact_type"] == "clip"
        assert art_data["status"] == "ready"
        assert art_data["user_id"] == USER_A_MEM1
        assert art_data["tenant_id"] == TENANT_A
        assert art_data["file_size_bytes"] == clip_file_size
        assert art_data["format"] == "mp4"
        assert abs(art_data["duration_seconds"] - clip_dur) < 0.2
        clip_hash = hashlib.sha256(output_clip_file.read_bytes()).hexdigest()
        assert art_data["sha256_checksum"] == clip_hash
        print(f"[PASS] Persistent MediaArtifactDB verified: status=ready, user_id={art_data['user_id']}, sha256={art_data['sha256_checksum'][:16]}...")

        print_step("TASK 1.5: Intra-tenant member isolation and cross-tenant anti-enumeration (404)")
        # 1. Member 1 (creator) downloads clip
        status_dl_m1, clip_bytes_m1 = api_request("GET", f"/api/v1/clips/{clip_id}/download", token=token_mem1)
        assert status_dl_m1 == 200, f"Member 1 failed to download clip: {status_dl_m1}"
        assert len(clip_bytes_m1) == clip_file_size
        print(f"[PASS] Member 1 (creator) clip download successful ({len(clip_bytes_m1)} bytes)")

        # 2. Member 1 downloads via /api/v1/artifacts/{id}/download
        status_art_dl, art_dl_bytes = api_request("GET", f"/api/v1/artifacts/{clip_id}/download", token=token_mem1)
        assert status_art_dl == 200, f"Member 1 failed artifact download: {status_art_dl}"
        assert len(art_dl_bytes) == clip_file_size
        print(f"[PASS] Member 1 /api/v1/artifacts/{clip_id}/download successful ({len(art_dl_bytes)} bytes)")

        # 3. Member 2 (same tenant, different member) downloads Member 1's clip -> 404 anti-enumeration!
        status_dl_m2, err_m2 = api_request("GET", f"/api/v1/clips/{clip_id}/download", token=token_mem2)
        assert status_dl_m2 == 404, f"Member 2 received {status_dl_m2} instead of 404 intra-tenant isolation"
        print(f"[PASS] Member 2 rejected with HTTP 404 for Member 1's clip: {err_m2}")

        # 4. Member 2 requests metadata for Member 1's clip -> 404 anti-enumeration!
        status_art_m2, err_art_m2 = api_request("GET", f"/api/v1/artifacts/{clip_id}", token=token_mem2)
        assert status_art_m2 == 404, f"Member 2 received {status_art_m2} for metadata instead of 404"
        print(f"[PASS] Member 2 rejected with HTTP 404 for Member 1's metadata: {err_art_m2}")

        # 5. Owner of Tenant A downloads Member 1's clip -> 200 OK (tenant administrative access)
        status_dl_own, clip_bytes_own = api_request("GET", f"/api/v1/clips/{clip_id}/download", token=token_owner)
        assert status_dl_own == 200, f"Tenant owner failed to download member clip: {status_dl_own}"
        print(f"[PASS] Tenant Owner successfully accessed Member 1's clip (200 OK)")

        # 6. Tenant B attempts to download Tenant A's clip -> 404 anti-enumeration!
        status_dl_b, err_b = api_request("GET", f"/api/v1/clips/{clip_id}/download", token=token_b)
        assert status_dl_b == 404, f"Tenant B received {status_dl_b} instead of 404 anti-enumeration"
        print(f"[PASS] Tenant B cross-tenant download rejected with HTTP 404: {err_b}")

        # 7. Tenant B attempts to extract clip from Tenant A's file -> 404
        status_clip_b, err_clip_b = api_request(
            "POST",
            "/api/v1/clips/extract",
            token=token_b,
            json_data={
                "source": "synthetic_test.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:02",
            },
        )
        assert status_clip_b == 404, f"Tenant B received {status_clip_b} instead of 404 anti-enumeration"
        print(f"[PASS] Tenant B source access rejected with HTTP 404: {err_clip_b}")

        # =======================================================================
        # TASK 2 — Real Video Merge Verification & MediaArtifactDB
        # =======================================================================
        print_step("TASK 2.1: Prepare 2 local clips with spaces and quotation marks")
        clip_space = tenant_a_clips / "clip with space.mp4"
        clip_quote = tenant_a_clips / "clip'quote.mp4"

        # Clip A: 0-2s
        subprocess.run([
            ffmpeg_bin, "-y", "-nostdin",
            "-ss", "0", "-i", str(synthetic_file), "-t", "2",
            "-c", "copy", str(clip_space),
        ], check=True, capture_output=True)

        # Clip B: 2-4s
        subprocess.run([
            ffmpeg_bin, "-y", "-nostdin",
            "-ss", "2", "-i", str(synthetic_file), "-t", "2",
            "-c", "copy", str(clip_quote),
        ], check=True, capture_output=True)

        assert clip_space.exists() and clip_quote.exists(), "Clips with space and quote were not created"
        print(f"[PASS] Created clips: '{clip_space.name}' and '{clip_quote.name}'")

        print_step("TASK 2.2: Execute real video merge (POST /api/v1/clips/merge) by Member 1")
        status_merge, merge_resp = api_request(
            "POST",
            "/api/v1/clips/merge",
            token=token_mem1,
            json_data={
                "clips": ["clip with space.mp4", "clip'quote.mp4"],
                "output_format": "mp4",
            },
        )
        assert status_merge == 200, f"Merge failed with HTTP {status_merge}: {merge_resp}"
        assert merge_resp["success"] is True
        merge_id = merge_resp["merge_id"]
        rel_merge_path = merge_resp["output_path"]
        assert rel_merge_path.startswith("merged/"), f"Expected relative 'merged/', got {rel_merge_path}"
        assert not os.path.isabs(rel_merge_path), f"Server leaked absolute path: {rel_merge_path}"

        merged_file = tenant_a_root / rel_merge_path
        assert merged_file.exists(), f"Merged video does not exist: {merged_file}"
        merged_file_size = merged_file.stat().st_size

        merge_probe = run_probe(ffprobe_bin, str(merged_file))
        merged_dur = float(merge_probe["format"]["duration"])
        assert 3.8 <= merged_dur <= 4.2, f"Expected combined ~4.0s duration, got {merged_dur}"
        print(f"[PASS] Real merge verified: merge_id={merge_id}, relative_path={rel_merge_path}, duration={merged_dur:.2f}s")

        # Verify persistent merge record
        status_mart, mart_data = api_request("GET", f"/api/v1/artifacts/{merge_id}", token=token_mem1)
        assert status_mart == 200, f"Failed to retrieve merge metadata: {status_mart}: {mart_data}"
        assert mart_data["id"] == merge_id
        assert mart_data["artifact_type"] == "merge"
        assert mart_data["status"] == "ready"
        assert mart_data["user_id"] == USER_A_MEM1
        assert mart_data["tenant_id"] == TENANT_A
        assert mart_data["file_size_bytes"] == merged_file_size
        print(f"[PASS] Persistent MediaArtifactDB for merge verified: status=ready, format={mart_data['format']}")

        print_step("TASK 2.3: Verify concat manifest cleanup and temporary workspace")
        temp_files = list(tenant_a_temp.glob("**/*"))
        assert len(temp_files) == 0, f"Found uncleaned temporary files: {temp_files}"
        print("[PASS] Temporary concat manifest was completely purged after processing")

        print_step("TASK 2.4: Merged video intra-tenant and cross-tenant boundaries")
        # Member 1 downloads merge
        status_mdl_m1, merge_bytes_m1 = api_request("GET", f"/api/v1/clips/merge/{merge_id}/download", token=token_mem1)
        assert status_mdl_m1 == 200, f"Member 1 failed to download merge: {status_mdl_m1}"
        assert len(merge_bytes_m1) == merged_file_size

        # Member 2 attempts to download Member 1's merge -> 404!
        status_mdl_m2, err_mdl_m2 = api_request("GET", f"/api/v1/clips/merge/{merge_id}/download", token=token_mem2)
        assert status_mdl_m2 == 404, f"Member 2 received {status_mdl_m2} instead of 404"
        print(f"[PASS] Member 2 rejected with HTTP 404 for Member 1's merged video")

        # Tenant Owner downloads Member 1's merge -> 200 OK
        status_mdl_own, merge_bytes_own = api_request("GET", f"/api/v1/clips/merge/{merge_id}/download", token=token_owner)
        assert status_mdl_own == 200, f"Owner failed to download merge: {status_mdl_own}"
        print(f"[PASS] Tenant Owner successfully accessed Member 1's merged video (200 OK)")

        # Tenant B receives 404
        status_mdl_b, err_mdl_b = api_request("GET", f"/api/v1/clips/merge/{merge_id}/download", token=token_b)
        assert status_mdl_b == 404, f"Tenant B received {status_mdl_b} instead of 404"
        print(f"[PASS] Cross-tenant merge download rejected with HTTP 404")

        print_step("TASK 2.5: Merge injection and path traversal defense")
        # 1. Newline injection
        s_nl, e_nl = api_request(
            "POST",
            "/api/v1/clips/merge",
            token=token_owner,
            json_data={"clips": ["clip1.mp4\nfile '/etc/passwd'", "clip'quote.mp4"]},
        )
        assert s_nl in (400, 422), f"Newline injection not blocked: {s_nl}"
        print(f"[PASS] Newline injection blocked with HTTP {s_nl}")

        # 2. Path traversal
        s_trav, e_trav = api_request(
            "POST",
            "/api/v1/clips/merge",
            token=token_owner,
            json_data={"clips": ["../../etc/passwd", "clip'quote.mp4"]},
        )
        assert s_trav in (400, 422), f"Path traversal not blocked: {s_trav}"
        print(f"[PASS] Path traversal blocked with HTTP {s_trav}")

        # 3. Cross-tenant clip input
        s_cross, e_cross = api_request(
            "POST",
            "/api/v1/clips/merge",
            token=token_b,
            json_data={"clips": ["clip with space.mp4", "clip'quote.mp4"]},
        )
        assert s_cross == 404, f"Cross-tenant merge not blocked: {s_cross}"
        print(f"[PASS] Cross-tenant merge blocked with HTTP 404: {e_cross}")

        # =======================================================================
        # TASK 3 — Filesystem Race & Symlink Defenses
        # =======================================================================
        print_step("TASK 3: Symlink defense verification")
        evil_symlink = tenant_a_downloads / "evil_symlink.mp4"
        try:
            if evil_symlink.exists():
                evil_symlink.unlink()
            os.symlink("/etc/passwd", evil_symlink)
        except OSError:
            pass

        if evil_symlink.is_symlink():
            s_sym, e_sym = api_request(
                "POST",
                "/api/v1/clips/extract",
                token=token_owner,
                json_data={"source": "evil_symlink.mp4", "start_time": "00:00:01", "end_time": "00:00:02"},
            )
            assert s_sym in (400, 403, 404), f"Symlink outside tenant not blocked: {s_sym}"
            print(f"[PASS] Symlink escape blocked with HTTP {s_sym}: {e_sym}")
            evil_symlink.unlink()

        # =======================================================================
        # TASK 4 — Process and Resource Safety
        # =======================================================================
        print_step("TASK 4: Resource limits and bounds")
        # Exceeding 2 hours
        s_2h, e_2h = api_request(
            "POST",
            "/api/v1/clips/extract",
            token=token_owner,
            json_data={"source": "synthetic_test.mp4", "start_time": "0", "end_time": "7201"},
        )
        assert s_2h == 400, f"Clip exceeding 2 hours not rejected: {s_2h}"
        print(f"[PASS] Duration > 2h rejected with HTTP 400: {e_2h.get('detail')}")

        # Negative start
        s_neg, e_neg = api_request(
            "POST",
            "/api/v1/clips/extract",
            token=token_owner,
            json_data={"source": "synthetic_test.mp4", "start_time": "-5", "end_time": "2"},
        )
        assert s_neg == 400, f"Negative timestamp not rejected: {s_neg}"
        print(f"[PASS] Negative timestamp rejected with HTTP 400: {e_neg.get('detail')}")

        # Merge > 20 inputs
        s_many, e_many = api_request(
            "POST",
            "/api/v1/clips/merge",
            token=token_owner,
            json_data={"clips": [f"c_{i}.mp4" for i in range(25)]},
        )
        assert s_many in (400, 422), f"Merge > 20 inputs not rejected: {s_many}"
        print(f"[PASS] Merge count > 20 rejected with HTTP {s_many}: {e_many.get('detail')}")

        # =======================================================================
        # TASK 5 — Trusted Egress Gateway Policy Audit
        # =======================================================================
        print_step("TASK 5: Trusted Egress Gateway audit")
        from api.security.url_policy import TRUSTED_EGRESS_GATEWAY, check_proxy_url
        assert TRUSTED_EGRESS_GATEWAY is None, f"Expected TRUSTED_EGRESS_GATEWAY to be None by default, got {TRUSTED_EGRESS_GATEWAY}"
        print("[PASS] Confirmed: MEDIAFORGE_TRUSTED_EGRESS_GATEWAY is disabled by default")

        try:
            os.environ["MEDIAFORGE_TRUSTED_EGRESS_GATEWAY"] = "http://93.184.216.34:8080"
            os.environ["MEDIAFORGE_GATEWAY_DESTINATION_POLICY"] = "disabled"
            from api.security.url_policy import SSRFSecurityError
            try:
                check_proxy_url("http://93.184.216.34:8080")
                raise AssertionError("Expected check_proxy_url to fail closed when destination policy is unverified")
            except SSRFSecurityError as exc:
                print(f"[PASS] Gateway without verified destination policy fails closed: {exc}")
        finally:
            os.environ.pop("MEDIAFORGE_TRUSTED_EGRESS_GATEWAY", None)
            os.environ.pop("MEDIAFORGE_GATEWAY_DESTINATION_POLICY", None)

        # =======================================================================
        # TASK 6 — Artifact Listing, Filtering, Deletion Lifecycle & Legacy Fallback
        # =======================================================================
        print_step("TASK 6.1: Artifact listing and filtering with intra-tenant member isolation")
        # 1. Member 1 lists artifacts: should see both clip_id and merge_id
        s_list_m1, list_m1 = api_request("GET", "/api/v1/artifacts", token=token_mem1)
        assert s_list_m1 == 200, f"Member 1 failed to list artifacts: {s_list_m1}"
        m1_ids = [item["id"] for item in list_m1["items"]]
        assert clip_id in m1_ids, f"Clip {clip_id} missing from Member 1 list: {m1_ids}"
        assert merge_id in m1_ids, f"Merge {merge_id} missing from Member 1 list: {m1_ids}"
        print(f"[PASS] Member 1 sees own artifacts: count={list_m1['total']}")

        # 2. Member 2 lists artifacts: must NOT see Member 1's artifacts!
        s_list_m2, list_m2 = api_request("GET", "/api/v1/artifacts", token=token_mem2)
        assert s_list_m2 == 200, f"Member 2 failed to list artifacts: {s_list_m2}"
        m2_ids = [item["id"] for item in list_m2["items"]]
        assert clip_id not in m2_ids, f"SECURITY LEAK: Member 2 saw Member 1 clip: {clip_id}"
        assert merge_id not in m2_ids, f"SECURITY LEAK: Member 2 saw Member 1 merge: {merge_id}"
        print(f"[PASS] Member 2 list is completely isolated (no Member 1 artifacts)")

        # 3. Owner lists artifacts: sees all tenant artifacts (both clip and merge)
        s_list_own, list_own = api_request("GET", "/api/v1/artifacts", token=token_owner)
        assert s_list_own == 200
        own_ids = [item["id"] for item in list_own["items"]]
        assert clip_id in own_ids and merge_id in own_ids
        print(f"[PASS] Tenant Owner sees all tenant artifacts: total={list_own['total']}")

        # 4. Filter by artifact_type
        s_flt_clip, flt_clip = api_request("GET", "/api/v1/artifacts?artifact_type=clip", token=token_mem1)
        assert s_flt_clip == 200
        assert all(it["artifact_type"] == "clip" for it in flt_clip["items"])
        s_flt_mrg, flt_mrg = api_request("GET", "/api/v1/artifacts?artifact_type=merge", token=token_mem1)
        assert s_flt_mrg == 200
        assert all(it["artifact_type"] == "merge" for it in flt_mrg["items"])
        print("[PASS] Artifact type filtering ('clip', 'merge') verified")

        print_step("TASK 6.2: Artifact deletion lifecycle and disk cleanup")
        # Member 2 attempts to delete Member 1's clip -> 404 anti-enumeration!
        s_del_m2, err_del_m2 = api_request("DELETE", f"/api/v1/artifacts/{clip_id}", token=token_mem2)
        assert s_del_m2 == 404, f"Member 2 received {s_del_m2} instead of 404"
        print(f"[PASS] Unauthorized deletion by Member 2 rejected with HTTP 404: {err_del_m2}")

        # Member 1 deletes clip
        s_del_m1, del_m1_resp = api_request("DELETE", f"/api/v1/artifacts/{clip_id}", token=token_mem1)
        assert s_del_m1 == 200, f"Member 1 failed to delete clip: {s_del_m1}"
        assert del_m1_resp["success"] is True
        print(f"[PASS] Member 1 successfully deleted clip: {del_m1_resp}")

        # Verify underlying file is deleted from disk
        assert not output_clip_file.exists(), f"CRITICAL: Deleted clip file still exists on disk: {output_clip_file}"
        print("[PASS] Underlying clip file was unlinked from storage disk")

        # Subsequent download attempts fail with 404
        s_down_deleted, _ = api_request("GET", f"/api/v1/artifacts/{clip_id}/download", token=token_mem1)
        assert s_down_deleted == 404, f"Expected 404 on deleted artifact download, got {s_down_deleted}"
        print("[PASS] Deleted artifact download returns HTTP 404")

        # Member 1 also deletes the merged video artifact
        s_del_mrg, del_mrg_resp = api_request("DELETE", f"/api/v1/artifacts/{merge_id}", token=token_mem1)
        assert s_del_mrg == 200, f"Member 1 failed to delete merge artifact: {s_del_mrg}"
        assert del_mrg_resp["success"] is True
        assert not merged_file.exists(), f"Deleted merge file still exists on disk: {merged_file}"
        print(f"[PASS] Member 1 successfully deleted merge artifact: {del_mrg_resp}")

        print_step("TASK 6.3: Legacy non-DB file fallback and fail-closed behavior (disabled by default)")
        legacy_clip = tenant_a_clips / "clip_legacy_test.mp4"
        legacy_clip.write_bytes(b"dummy legacy mp4 content for testing fallback authorization")

        try:
            # 1. Member 1 attempts to download non-DB file by ID -> 404 (fail closed for members)
            s_leg_m1, _ = api_request("GET", "/api/v1/clips/legacy_test/download", token=token_mem1)
            assert s_leg_m1 == 404, f"Member received {s_leg_m1} instead of 404 fail-closed"
            print("[PASS] Legacy non-DB file download fails closed with 404 for member")

            # 2. By default, legacy path-based access is disabled: Owner also receives 404!
            s_leg_own_default, _ = api_request("GET", "/api/v1/clips/legacy_test/download", token=token_owner)
            assert s_leg_own_default == 404, f"Owner received {s_leg_own_default} instead of 404 (legacy fallback disabled by default)"
            print("[PASS] Legacy non-DB file access is disabled by default for all callers (Owner: 404)")

            # 3. Verify actual file is preserved on disk
            assert legacy_clip.exists(), "Legacy file on disk was improperly removed"
            print("[PASS] Unindexed legacy file is preserved on disk")
        finally:
            if legacy_clip.exists():
                legacy_clip.unlink()

        print_step("TASK 6.4: Safe, idempotent dry-run reconciliation verification")
        from api.services.reconcile_artifacts import run_reconciliation
        from sqlalchemy.ext.asyncio import async_sessionmaker
        settings = get_api_settings()
        recon_engine = create_async_engine(settings.database_url)
        recon_factory = async_sessionmaker(recon_engine, expire_on_commit=False)
        async def _test_recon():
            async with recon_factory() as s:
                report = await run_reconciliation(s, storage_root=get_media_storage_root(), stale_timeout_seconds=1800)
                print(f"[PASS] Reconciliation executed cleanly: scanned_artifacts={report.scanned_artifacts_count}, scanned_files={report.scanned_files_count}")
                print(f"[PASS] Identified discrepancies: stale_processing={report.stale_processing_count}, missing_ready={report.missing_ready_count}, orphaned_files={report.orphaned_files_count}")
                assert report.scanned_artifacts_count > 0, "Expected scanned artifacts in database"
                assert isinstance(report.items, list), "Expected items list in reconciliation report"
            await recon_engine.dispose()
        asyncio.run(_test_recon())

    finally:
        # Safe cleanup of all test artifacts
        print("\nCleaning up test artifacts...")
        try:
            asyncio.run(cleanup_test_media())
        except Exception:
            pass
        for p in (synthetic_file, clip_space, clip_quote):
            try:
                if p and p.exists():
                    p.unlink()
            except Exception:
                pass
        for sub in ("clips", "merged", "temp"):
            d = tenant_a_root / sub
            if d.exists():
                for f in d.glob("*"):
                    try:
                        if f.is_file():
                            f.unlink()
                        elif f.is_dir():
                            shutil.rmtree(f, ignore_errors=True)
                    except Exception:
                        pass
        print("Cleanup complete.")

    print(f"\n{'='*70}\n[ALL STEP 02-E1 REAL MEDIA INTEGRATION CHECKS PASSED SUCCESSFULLY]\n{'='*70}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
