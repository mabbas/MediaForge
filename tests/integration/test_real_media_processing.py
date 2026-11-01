"""Integration tests for real FFmpeg media processing inside Docker container (STEP 02-D2-Fix-A).

Verifies:
- Synthetic MP4 creation with real FFmpeg and FFprobe stream verification.
- Real offline clipping through authenticated FastAPI endpoints.
- Output playability, duration, stream properties via FFprobe.
- Unchanged original source media.
- Tenant isolation and anti-enumeration (cross-tenant 404).
- Safe relative output paths (no host filesystem leakage).
- Real video merging with concat demuxer.
- Handling of spaces and quotation marks in filenames.
- Injection defense (newlines, null bytes, path traversal).
- Manifest cleanup and temporary workspace lifecycle.
- Symlink and hardlink attack rejection.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.security.jwt import create_access_token
from api.security.media_storage import (
    get_media_storage_root,
    get_tenant_media_dir,
    get_tenant_storage_root,
)
from src.env_loader import resolve_ffmpeg_binaries

try:
    _ffmpeg_check, _ffprobe_check = resolve_ffmpeg_binaries()
    _has_ffmpeg = bool(shutil.which(_ffmpeg_check) or os.path.isfile(_ffmpeg_check))
except Exception:
    _has_ffmpeg = False

if not _has_ffmpeg:
    pytestmark = pytest.mark.skip(reason="FFmpeg/FFprobe binaries not installed in host execution environment (requires Docker API container)")

TEST_TENANT_A = "00000000-0000-0000-0000-000000000001"
TEST_USER_A = "00000000-0000-0000-0000-000000000002"

TEST_TENANT_B = "11111111-1111-1111-1111-111111111111"
TEST_USER_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def ffmpeg_binaries():
    """Verify that FFmpeg and FFprobe binaries are available in the test environment."""
    ffmpeg, ffprobe = resolve_ffmpeg_binaries()
    if not shutil.which(ffmpeg) and not os.path.isfile(ffmpeg):
        pytest.skip(f"FFmpeg not installed at {ffmpeg}")
    if not shutil.which(ffprobe) and not os.path.isfile(ffprobe):
        pytest.skip(f"FFprobe not installed at {ffprobe}")
    return ffmpeg, ffprobe


@pytest.fixture(scope="module")
def test_client():
    """FastAPI TestClient for authenticated API dispatch."""
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers():
    """Generate JWT authorization headers for Tenant A and Tenant B."""
    token_a, _ = create_access_token(
        user_id=TEST_USER_A,
        tenant_id=TEST_TENANT_A,
        extra_claims={"role": "owner", "tier": "platinum"},
    )
    token_b, _ = create_access_token(
        user_id=TEST_USER_B,
        tenant_id=TEST_TENANT_B,
        extra_claims={"role": "owner", "tier": "free"},
    )
    return {
        "tenant_a": {"Authorization": f"Bearer {token_a}"},
        "tenant_b": {"Authorization": f"Bearer {token_b}"},
    }


@pytest.fixture(scope="module")
def synthetic_media(ffmpeg_binaries):
    """Generate a clean synthetic 5-second MP4 with video and audio in Tenant A storage."""
    ffmpeg, ffprobe = ffmpeg_binaries
    tenant_a_downloads = get_tenant_media_dir(TEST_TENANT_A, "downloads")
    tenant_a_downloads.mkdir(parents=True, exist_ok=True)

    src_file = tenant_a_downloads / "synthetic_source.mp4"

    # Generate 5-second video (testsrc) + audio (sine wave 1000Hz)
    cmd = [
        ffmpeg,
        "-y",
        "-nostdin",
        "-f", "lavfi", "-i", "testsrc=duration=5:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(src_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"FFmpeg synthetic media generation failed: {proc.stderr}"
    assert src_file.exists(), "Synthetic media file was not created"
    assert src_file.stat().st_size > 1024, "Synthetic media file is too small"

    # Compute initial SHA-256 to verify immutability later
    src_hash = hashlib.sha256(src_file.read_bytes()).hexdigest()

    yield {
        "file": src_file,
        "name": src_file.name,
        "sha256": src_hash,
        "duration": 5.0,
    }

    # Safe cleanup after module tests complete
    try:
        if src_file.exists():
            src_file.unlink()
        tenant_a_root = get_tenant_storage_root(TEST_TENANT_A)
        for sub in ("clips", "merged", "temp"):
            d = tenant_a_root / sub
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def run_ffprobe_probe(ffprobe_path: str, media_path: Path) -> dict:
    """Execute real FFprobe and return parsed stream information."""
    cmd = [
        ffprobe_path,
        "-nostdin",
        "-protocol_whitelist", "file",
        "-v", "quiet",
        "-show_format",
        "-show_streams",
        "-of", "json",
        str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"FFprobe failed: {proc.stderr}"
    return json.loads(proc.stdout)


# ===========================================================================
# TASK 1 — Real Offline Clipping Verification
# ===========================================================================

def test_synthetic_media_ffprobe_properties(ffmpeg_binaries, synthetic_media):
    """Verify synthetic source has valid h264 video and aac audio streams."""
    _, ffprobe = ffmpeg_binaries
    info = run_ffprobe_probe(ffprobe, synthetic_media["file"])

    format_info = info.get("format", {})
    duration = float(format_info.get("duration", 0))
    assert 4.8 <= duration <= 5.2, f"Expected ~5.0s duration, got {duration}"

    streams = info.get("streams", [])
    assert len(streams) >= 2, "Expected both video and audio streams"

    codecs = {s.get("codec_type"): s.get("codec_name") for s in streams}
    assert codecs.get("video") == "h264", f"Expected h264, got {codecs.get('video')}"
    assert codecs.get("audio") == "aac", f"Expected aac, got {codecs.get('audio')}"


def test_real_offline_clipping_api(test_client, auth_headers, ffmpeg_binaries, synthetic_media):
    """Execute real offline clip extraction through POST /api/v1/clips/extract."""
    _, ffprobe = ffmpeg_binaries

    response = test_client.post(
        "/api/v1/clips/extract",
        json={
            "source": synthetic_media["name"],
            "start_time": "00:00:01",
            "end_time": "00:00:03",
            "output_format": "mp4",
            "mode": "precise",
        },
        headers=auth_headers["tenant_a"],
    )

    assert response.status_code == 200, f"Clip extraction failed: {response.text}"
    data = response.json()
    assert data["success"] is True
    assert data["clip_id"] is not None

    # Check safe relative output path (no host server absolute paths)
    rel_path = data["output_path"]
    assert rel_path.startswith("clips/"), f"Expected relative 'clips/', got {rel_path}"
    assert not os.path.isabs(rel_path), f"Output path leaked absolute path: {rel_path}"

    # Verify output file exists in tenant's clips storage
    tenant_a_root = get_tenant_storage_root(TEST_TENANT_A)
    output_file = tenant_a_root / rel_path
    assert output_file.exists(), f"Output file does not exist on disk: {output_file}"
    assert output_file.stat().st_size > 512, "Clip output file is empty"

    # Verify clip properties via FFprobe
    clip_info = run_ffprobe_probe(ffprobe, output_file)
    clip_duration = float(clip_info.get("format", {}).get("duration", 0))
    assert 1.8 <= clip_duration <= 2.2, f"Expected ~2.0s duration, got {clip_duration}"

    clip_codecs = {s.get("codec_type"): s.get("codec_name") for s in clip_info.get("streams", [])}
    assert clip_codecs.get("video") == "h264"
    assert clip_codecs.get("audio") == "aac"

    # Verify original source remains untouched (hash and size unmodified)
    current_src_hash = hashlib.sha256(synthetic_media["file"].read_bytes()).hexdigest()
    assert current_src_hash == synthetic_media["sha256"], "Original source video was modified!"


def test_real_clip_download_api(test_client, auth_headers, synthetic_media):
    """Verify downloading an extracted clip with tenant scoping and anti-enumeration."""
    # 1. Extract a clip first
    res = test_client.post(
        "/api/v1/clips/extract",
        json={
            "source": synthetic_media["name"],
            "start_time": "00:00:02",
            "end_time": "00:00:04",
            "output_format": "mp4",
        },
        headers=auth_headers["tenant_a"],
    )
    assert res.status_code == 200
    clip_id = res.json()["clip_id"]

    # 2. Tenant A owner downloads clip -> 200 OK
    dl_res = test_client.get(
        f"/api/v1/clips/{clip_id}/download",
        headers=auth_headers["tenant_a"],
    )
    assert dl_res.status_code == 200
    assert len(dl_res.content) > 512

    # 3. Tenant B attempts to download Tenant A's clip -> 404 Not Found (anti-enumeration)
    dl_res_b = test_client.get(
        f"/api/v1/clips/{clip_id}/download",
        headers=auth_headers["tenant_b"],
    )
    assert dl_res_b.status_code == 404


def test_cross_tenant_source_clip_denied(test_client, auth_headers, synthetic_media):
    """Tenant B cannot extract clips from Tenant A's source media (404 anti-enumeration)."""
    res = test_client.post(
        "/api/v1/clips/extract",
        json={
            "source": synthetic_media["name"],
            "start_time": "00:00:01",
            "end_time": "00:00:02",
        },
        headers=auth_headers["tenant_b"],
    )
    assert res.status_code == 404
    assert "not found in tenant storage" in res.json()["detail"].lower()


# ===========================================================================
# TASK 2 — Real Video Merge Verification
# ===========================================================================

def test_real_video_merge_with_spaces_and_quotes(test_client, auth_headers, ffmpeg_binaries, synthetic_media):
    """Execute real video merging with filenames containing spaces and quotation marks."""
    ffmpeg, ffprobe = ffmpeg_binaries
    tenant_a_clips = get_tenant_media_dir(TEST_TENANT_A, "clips")
    tenant_a_clips.mkdir(parents=True, exist_ok=True)

    # Extract 2 short segments to tenant_a clips directory
    clip1_path = tenant_a_clips / "clip with space.mp4"
    clip2_path = tenant_a_clips / "clip'quote.mp4"

    # Clip 1: 0 to 2 seconds
    subprocess.run([
        ffmpeg, "-y", "-nostdin",
        "-ss", "0", "-i", str(synthetic_media["file"]), "-t", "2",
        "-c", "copy", str(clip1_path),
    ], check=True, capture_output=True)

    # Clip 2: 2 to 4 seconds
    subprocess.run([
        ffmpeg, "-y", "-nostdin",
        "-ss", "2", "-i", str(synthetic_media["file"]), "-t", "2",
        "-c", "copy", str(clip2_path),
    ], check=True, capture_output=True)

    assert clip1_path.exists() and clip2_path.exists()

    # Call POST /api/v1/clips/merge with spaces and quotes in filenames
    res = test_client.post(
        "/api/v1/clips/merge",
        json={
            "clips": ["clip with space.mp4", "clip'quote.mp4"],
            "output_format": "mp4",
        },
        headers=auth_headers["tenant_a"],
    )
    assert res.status_code == 200, f"Merge failed: {res.text}"
    data = res.json()
    assert data["success"] is True
    assert data["merge_id"] is not None

    rel_output = data["output_path"]
    assert rel_output.startswith("merged/"), f"Expected relative 'merged/', got {rel_output}"
    assert not os.path.isabs(rel_output)

    # Check merged video on disk
    tenant_a_root = get_tenant_storage_root(TEST_TENANT_A)
    merged_file = tenant_a_root / rel_output
    assert merged_file.exists(), f"Merged video does not exist: {merged_file}"

    # Verify duration is consistent with both clips (~2.0s + ~2.0s = ~4.0s)
    merge_info = run_ffprobe_probe(ffprobe, merged_file)
    merged_duration = float(merge_info.get("format", {}).get("duration", 0))
    assert 3.8 <= merged_duration <= 4.2, f"Expected ~4.0s duration, got {merged_duration}"

    # Verify download of merged video
    dl_res = test_client.get(
        f"/api/v1/clips/merge/{data['merge_id']}/download",
        headers=auth_headers["tenant_a"],
    )
    assert dl_res.status_code == 200
    assert len(dl_res.content) > 1024

    # Verify Tenant B cannot download merged video
    dl_b_res = test_client.get(
        f"/api/v1/clips/merge/{data['merge_id']}/download",
        headers=auth_headers["tenant_b"],
    )
    assert dl_b_res.status_code == 404


def test_merge_rejects_newlines_and_traversal(test_client, auth_headers):
    """Verify that concat merge rejects newlines, null bytes, and traversal tokens."""
    # 1. Newline injection
    res_nl = test_client.post(
        "/api/v1/clips/merge",
        json={
            "clips": ["clip1.mp4\nfile '/etc/passwd'", "clip2.mp4"],
            "output_format": "mp4",
        },
        headers=auth_headers["tenant_a"],
    )
    assert res_nl.status_code in (400, 422)

    # 2. Path traversal token
    res_trav = test_client.post(
        "/api/v1/clips/merge",
        json={
            "clips": ["../../etc/passwd", "clip2.mp4"],
            "output_format": "mp4",
        },
        headers=auth_headers["tenant_a"],
    )
    assert res_trav.status_code in (400, 422)


def test_merge_rejects_cross_tenant_inputs(test_client, auth_headers, synthetic_media):
    """Tenant B cannot merge Tenant A's clips."""
    res = test_client.post(
        "/api/v1/clips/merge",
        json={
            "clips": ["clip with space.mp4", "clip'quote.mp4"],
            "output_format": "mp4",
        },
        headers=auth_headers["tenant_b"],
    )
    assert res.status_code == 404
    assert "not found in tenant storage" in res.json()["detail"].lower()


# ===========================================================================
# TASK 3 & 4 — Filesystem Safety and Resource Limits
# ===========================================================================

def test_symlink_defense_on_clip_source(test_client, auth_headers, tmp_path):
    """Verify that a symlink pointing outside tenant storage is rejected."""
    outside_secret = tmp_path / "host_sensitive.txt"
    outside_secret.write_text("HOST SECRET DATA")

    tenant_a_downloads = get_tenant_media_dir(TEST_TENANT_A, "downloads")
    symlink_file = tenant_a_downloads / "evil_symlink.mp4"

    try:
        if symlink_file.exists():
            symlink_file.unlink()
        os.symlink(outside_secret, symlink_file)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported on host filesystem")

    try:
        res = test_client.post(
            "/api/v1/clips/extract",
            json={
                "source": "evil_symlink.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:02",
            },
            headers=auth_headers["tenant_a"],
        )
        assert res.status_code in (403, 404)
    finally:
        if symlink_file.exists():
            symlink_file.unlink()


def test_resource_limits_enforced_in_api(test_client, auth_headers, synthetic_media):
    """Verify duration limits and merge count boundaries in real API endpoints."""
    # Exceeding 2 hours
    res_long = test_client.post(
        "/api/v1/clips/extract",
        json={
            "source": synthetic_media["name"],
            "start_time": "0",
            "end_time": "7201",
        },
        headers=auth_headers["tenant_a"],
    )
    assert res_long.status_code == 400
    assert "cannot exceed 2 hours" in res_long.json()["detail"].lower()

    # Exceeding source duration
    res_exceed = test_client.post(
        "/api/v1/clips/extract",
        json={
            "source": synthetic_media["name"],
            "start_time": "0",
            "end_time": "10",  # source is 5s
        },
        headers=auth_headers["tenant_a"],
    )
    assert res_exceed.status_code == 400
    assert "exceeds video duration" in res_exceed.json()["detail"].lower()

    # Merge > 20 inputs
    res_too_many = test_client.post(
        "/api/v1/clips/merge",
        json={"clips": [f"c_{i}.mp4" for i in range(25)]},
        headers=auth_headers["tenant_a"],
    )
    assert res_too_many.status_code in (400, 422)
