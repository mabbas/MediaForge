"""Unit tests for MediaForge filesystem isolation, FFmpeg security, and safe offline clipping (STEP 02-D2)."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.dependencies import CurrentUser
from api.main import app
from api.security.media_storage import (
    MAX_MEDIA_INPUT_SIZE_MB,
    MAX_MERGE_INPUTS,
    MediaSecurityError,
    build_safe_concat_manifest,
    check_untrusted_path_traversal,
    generate_safe_output_path,
    get_media_storage_root,
    get_tenant_media_dir,
    get_tenant_storage_root,
    resolve_and_authorize_clip_source,
    resolve_tenant_artifact_path,
    tenant_temp_workspace,
    validate_safe_filename,
    validate_tenant_identifier,
)


@pytest.fixture
def mock_storage_root(tmp_path, monkeypatch):
    """Isolate media storage root to a temporary directory."""
    media_root = tmp_path / "media_storage"
    media_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GID_DOWNLOAD__OUTPUT_DIRECTORY", str(media_root))
    return media_root


@pytest.fixture
def tenant_a_fixture(mock_storage_root):
    """Set up storage and a sample video for Tenant A."""
    tenant_id = "tenant-a-uuid"
    t_root = get_tenant_storage_root(tenant_id)
    downloads_dir = get_tenant_media_dir(tenant_id, "downloads")
    clips_dir = get_tenant_media_dir(tenant_id, "clips")
    merged_dir = get_tenant_media_dir(tenant_id, "merged")

    # Create dummy media files
    video_a = downloads_dir / "video_a.mp4"
    video_a.write_bytes(b"\x00" * 1024)

    clip_a1 = clips_dir / "clip_a1.mp4"
    clip_a1.write_bytes(b"\x00" * 1024)

    clip_a2 = clips_dir / "clip_a2.mp4"
    clip_a2.write_bytes(b"\x00" * 1024)

    return {
        "tenant_id": tenant_id,
        "root": t_root,
        "video": video_a,
        "clip1": clip_a1,
        "clip2": clip_a2,
    }


@pytest.fixture
def tenant_b_fixture(mock_storage_root):
    """Set up storage and a sample video for Tenant B."""
    tenant_id = "tenant-b-uuid"
    t_root = get_tenant_storage_root(tenant_id)
    downloads_dir = get_tenant_media_dir(tenant_id, "downloads")
    clips_dir = get_tenant_media_dir(tenant_id, "clips")

    video_b = downloads_dir / "secret_b.mp4"
    video_b.write_bytes(b"\x00" * 2048)

    clip_b = clips_dir / "clip_b_private.mp4"
    clip_b.write_bytes(b"\x00" * 2048)

    return {
        "tenant_id": tenant_id,
        "root": t_root,
        "video": video_b,
        "clip": clip_b,
    }


@pytest.fixture
def client_tenant_a(tenant_a_fixture):
    """TestClient authenticated as Tenant A member."""
    user = CurrentUser(
        user_id="user-a-1",
        email="user_a@example.com",
        tenant_id=tenant_a_fixture["tenant_id"],
        role="owner",
        scopes=["clips:write", "clips:read", "downloads:write", "downloads:read"],
    )
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import AsyncSession
    from api.database.connection import get_session
    from api.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: AsyncMock(spec=AsyncSession)

    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.clear()


# ===========================================================================
# 1. Path Traversal & Reusable Path Helpers
# ===========================================================================

def test_validate_tenant_identifier():
    """Verify tenant ID format rules."""
    assert validate_tenant_identifier("tenant-123_abc") == "tenant-123_abc"
    with pytest.raises(MediaSecurityError):
        validate_tenant_identifier("../tenant")
    with pytest.raises(MediaSecurityError):
        validate_tenant_identifier("tenant/sub")
    with pytest.raises(MediaSecurityError):
        validate_tenant_identifier("tenant\\sub")
    with pytest.raises(MediaSecurityError):
        validate_tenant_identifier("")


def test_validate_safe_filename():
    """Verify filename sanitization against traversal and device names."""
    assert validate_safe_filename("video_test.mp4") == "video_test.mp4"

    # Traversal & separators
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("../evil.mp4")
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("sub/file.mp4")
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("sub\\file.mp4")
    with pytest.raises(MediaSecurityError):
        validate_safe_filename(".")
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("..")

    # Control chars & null bytes
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("video\0.mp4")
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("video\n.mp4")

    # Windows reserved names & ADS
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("con.mp4")
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("NUL")
    with pytest.raises(MediaSecurityError):
        validate_safe_filename("video.mp4:stream")


def test_check_untrusted_path_traversal():
    """Verify rejection of traversal and absolute indicators."""
    check_untrusted_path_traversal("video.mp4")
    check_untrusted_path_traversal("downloads/video.mp4")

    with pytest.raises(MediaSecurityError):
        check_untrusted_path_traversal("/etc/passwd")
    with pytest.raises(MediaSecurityError):
        check_untrusted_path_traversal("\\Windows\\win.ini")
    with pytest.raises(MediaSecurityError):
        check_untrusted_path_traversal("C:\\secret.txt")
    with pytest.raises(MediaSecurityError):
        check_untrusted_path_traversal("../deploy/.env")
    with pytest.raises(MediaSecurityError):
        check_untrusted_path_traversal("video.mp4\0")


def test_resolve_tenant_artifact_path(tenant_a_fixture):
    """Verify tenant artifact resolution strictly enforces containment."""
    tid = tenant_a_fixture["tenant_id"]

    resolved = resolve_tenant_artifact_path("video_a.mp4", tid, "downloads")
    assert resolved == tenant_a_fixture["video"]

    with pytest.raises(MediaSecurityError):
        resolve_tenant_artifact_path("../../deploy/.env", tid, "downloads")

    with pytest.raises(MediaSecurityError):
        resolve_tenant_artifact_path("/etc/passwd", tid, "downloads")


# ===========================================================================
# 2. Source Media Authorization & Anti-Enumeration
# ===========================================================================

def test_resolve_and_authorize_clip_source_success(tenant_a_fixture):
    """Authorized file within tenant storage is successfully resolved."""
    tid = tenant_a_fixture["tenant_id"]

    # Relative to tenant root
    resolved = resolve_and_authorize_clip_source("downloads/video_a.mp4", tid)
    assert resolved == tenant_a_fixture["video"]

    # Direct filename search
    resolved2 = resolve_and_authorize_clip_source("video_a.mp4", tid)
    assert resolved2 == tenant_a_fixture["video"]

    # Full container/internal path matching tenant root (e.g. from DownloadJobDB.file_path)
    resolved3 = resolve_and_authorize_clip_source(str(tenant_a_fixture["video"]), tid)
    assert resolved3 == tenant_a_fixture["video"]


def test_resolve_and_authorize_clip_source_cross_tenant_denied(tenant_a_fixture, tenant_b_fixture):
    """Tenant A cannot resolve Tenant B's files even if the filename is known."""
    tid_a = tenant_a_fixture["tenant_id"]

    # Tenant B has secret_b.mp4. Tenant A attempts to access it
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        resolve_and_authorize_clip_source("secret_b.mp4", tid_a)
    assert exc_info.value.status_code == 404
    assert "not found in tenant storage" in exc_info.value.detail


def test_resolve_and_authorize_clip_source_absolute_path_rejected(tenant_a_fixture):
    """Absolute paths are rejected with HTTP 400 without probing the filesystem."""
    tid = tenant_a_fixture["tenant_id"]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        resolve_and_authorize_clip_source("/etc/passwd", tid)
    assert exc_info.value.status_code == 400
    assert "prohibited" in exc_info.value.detail.lower()

    with pytest.raises(HTTPException) as exc_info2:
        resolve_and_authorize_clip_source("C:\\Windows\\System32\\cmd.exe", tid)
    assert exc_info2.value.status_code == 400


def test_resolve_and_authorize_clip_source_size_limit(tenant_a_fixture, monkeypatch):
    """Files exceeding MAX_MEDIA_INPUT_SIZE_MB are rejected before processing."""
    tid = tenant_a_fixture["tenant_id"]
    monkeypatch.setattr("api.security.media_storage.MAX_MEDIA_INPUT_SIZE_MB", 0.0001)  # tiny limit ~100 bytes

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        resolve_and_authorize_clip_source("video_a.mp4", tid)
    assert exc_info.value.status_code == 400
    assert "exceeds maximum allowed limit" in exc_info.value.detail.lower()


# ===========================================================================
# 3. Symlink Escape Protection
# ===========================================================================

def test_symlink_escape_rejected(tenant_a_fixture, tmp_path):
    """Symlinks created inside tenant storage pointing outside are rejected."""
    tid = tenant_a_fixture["tenant_id"]
    t_root = tenant_a_fixture["root"]

    outside_file = tmp_path / "outside_host_file.txt"
    outside_file.write_text("sensitive host data")

    symlink_path = t_root / "downloads" / "escaped_link.mp4"
    try:
        os.symlink(outside_file, symlink_path)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported on this host environment")

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        resolve_and_authorize_clip_source("escaped_link.mp4", tid)
    assert exc_info.value.status_code in (403, 404)


# ===========================================================================
# 4. Safe Concat Manifest Generation & Injection Defense
# ===========================================================================

def test_build_safe_concat_manifest_success(tenant_a_fixture):
    """Verify safe concat manifest writes valid format."""
    tid = tenant_a_fixture["tenant_id"]
    clip1 = tenant_a_fixture["clip1"]
    clip2 = tenant_a_fixture["clip2"]
    manifest_path = tenant_a_fixture["root"] / "temp" / "concat_test.txt"

    build_safe_concat_manifest([clip1, clip2], tid, manifest_path)
    content = manifest_path.read_text(encoding="utf-8")

    assert f"file '{clip1}'" in content
    assert f"file '{clip2}'" in content


def test_build_safe_concat_manifest_single_quote_escaping(tenant_a_fixture):
    """Verify single quotes in clip path are escaped with '\\''."""
    tid = tenant_a_fixture["tenant_id"]
    clip_with_quote = tenant_a_fixture["root"] / "clips" / "my'clip'quote.mp4"
    clip_with_quote.write_bytes(b"\x00" * 100)

    manifest_path = tenant_a_fixture["root"] / "temp" / "concat_quote.txt"
    build_safe_concat_manifest([clip_with_quote], tid, manifest_path)
    content = manifest_path.read_text(encoding="utf-8")

    assert "'\\''" in content
    assert "my'\\''clip'\\''quote.mp4" in content


def test_build_safe_concat_manifest_newline_injection_rejected(tenant_a_fixture):
    """Verify that filenames attempting newline injection raise MediaSecurityError."""
    tid = tenant_a_fixture["tenant_id"]
    fake_path = MagicMock(spec=Path)
    fake_path.resolve.return_value = fake_path
    fake_path.is_file.return_value = True
    fake_path.is_relative_to.return_value = True
    fake_path.__str__.return_value = str(tenant_a_fixture["clip1"]) + "\nfile '/etc/passwd'\n"

    manifest_path = tenant_a_fixture["root"] / "temp" / "concat_inj.txt"
    with pytest.raises(MediaSecurityError) as exc_info:
        build_safe_concat_manifest([fake_path], tid, manifest_path)
    assert "prohibited newline" in str(exc_info.value).lower() or "illegal newline" in str(exc_info.value).lower()


def test_build_safe_concat_manifest_cross_tenant_containment(tenant_a_fixture, tenant_b_fixture):
    """Concat manifest generation rejects clips belonging to another tenant."""
    tid_a = tenant_a_fixture["tenant_id"]
    clip_b = tenant_b_fixture["clip"]
    manifest_path = tenant_a_fixture["root"] / "temp" / "concat_cross.txt"

    with pytest.raises(MediaSecurityError) as exc_info:
        build_safe_concat_manifest([clip_b], tid_a, manifest_path)
    assert "outside tenant authorized storage" in str(exc_info.value).lower()


# ===========================================================================
# 5. Temporary Workspace Cleanup
# ===========================================================================

def test_tenant_temp_workspace_cleanup(tenant_a_fixture):
    """Ensure temporary directories and processing artifacts are cleaned up."""
    tid = tenant_a_fixture["tenant_id"]
    created_dir = None

    with tenant_temp_workspace(tid) as ws:
        created_dir = ws
        assert ws.exists()
        assert ws.is_dir()
        temp_file = ws / "chunk.tmp"
        temp_file.write_bytes(b"temporary intermediate data")
        assert temp_file.exists()

    assert not created_dir.exists()


def test_tenant_temp_workspace_cleanup_on_exception(tenant_a_fixture):
    """Ensure temp workspace is cleaned up even if an unhandled exception occurs."""
    tid = tenant_a_fixture["tenant_id"]
    created_dir = None

    try:
        with tenant_temp_workspace(tid) as ws:
            created_dir = ws
            (ws / "artifact.dat").write_bytes(b"data")
            raise RuntimeError("Simulated processing failure")
    except RuntimeError:
        pass

    assert created_dir is not None
    assert not created_dir.exists()


# ===========================================================================
# 6. Safe Output Paths & Overwrite Prevention
# ===========================================================================

def test_generate_safe_output_path(tenant_a_fixture):
    """Verify output paths are collision-resistant and return relative safe paths."""
    tid = tenant_a_fixture["tenant_id"]
    abs_path1, rel_path1, cid1 = generate_safe_output_path(tid, "clips", "test", "mp4")
    abs_path2, rel_path2, cid2 = generate_safe_output_path(tid, "clips", "test", "mp4")

    assert abs_path1 != abs_path2
    assert cid1 != cid2
    assert rel_path1.startswith("clips/")
    assert rel_path2.startswith("clips/")
    # Ensure relative path does NOT leak server base root
    assert not os.path.isabs(rel_path1)


# ===========================================================================
# 7. API Endpoints: Clip Extraction Security Boundary
# ===========================================================================

def test_api_clip_extract_rejects_path_traversal(client_tenant_a):
    """POST /api/v1/clips/extract rejects traversal in source."""
    res = client_tenant_a.post(
        "/api/v1/clips/extract",
        json={
            "source": "../../etc/passwd",
            "start_time": "00:00:10",
            "end_time": "00:00:20",
        },
    )
    assert res.status_code == 400
    assert "invalid source" in res.json()["detail"].lower() or "prohibited" in res.json()["detail"].lower()


def test_api_clip_extract_rejects_absolute_path(client_tenant_a):
    """POST /api/v1/clips/extract rejects absolute host paths."""
    res = client_tenant_a.post(
        "/api/v1/clips/extract",
        json={
            "source": "/etc/shadow",
            "start_time": "00:00:10",
            "end_time": "00:00:20",
        },
    )
    assert res.status_code == 400
    assert "prohibited" in res.json()["detail"].lower()


def test_api_clip_extract_cross_tenant_isolation(client_tenant_a, tenant_b_fixture):
    """Tenant A cannot clip Tenant B's files (404 anti-enumeration)."""
    res = client_tenant_a.post(
        "/api/v1/clips/extract",
        json={
            "source": "secret_b.mp4",
            "start_time": "00:00:10",
            "end_time": "00:00:20",
        },
    )
    assert res.status_code == 404
    assert "not found in tenant storage" in res.json()["detail"].lower()


def test_api_clip_extract_duration_limits(client_tenant_a, tenant_a_fixture):
    """POST /api/v1/clips/extract rejects clips exceeding 2 hours or negative timestamps."""
    # Negative timestamp
    res_neg = client_tenant_a.post(
        "/api/v1/clips/extract",
        json={
            "source": "video_a.mp4",
            "start_time": "-10",
            "end_time": "20",
        },
    )
    assert res_neg.status_code == 400

    # Start >= End
    res_inv = client_tenant_a.post(
        "/api/v1/clips/extract",
        json={
            "source": "video_a.mp4",
            "start_time": "30",
            "end_time": "10",
        },
    )
    assert res_inv.status_code == 400

    # Exceeding 2 hours (7200s)
    res_long = client_tenant_a.post(
        "/api/v1/clips/extract",
        json={
            "source": "video_a.mp4",
            "start_time": "0",
            "end_time": "7201",
        },
    )
    assert res_long.status_code == 400


def test_api_clip_extract_legitimate_offline_clip(client_tenant_a, tenant_a_fixture):
    """POST /api/v1/clips/extract succeeds for authorized offline clipping and returns safe relative path."""
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.clip_id = "testcid123"
    mock_result.source = "downloads/video_a.mp4"
    mock_result.output_path = "/app/downloads/tenant-a-uuid/clips/out.mp4"
    mock_result.start_time = "00:00:01"
    mock_result.end_time = "00:00:05"
    mock_result.duration_seconds = 4.0
    mock_result.file_size_bytes = 1024
    mock_result.file_size_human = "1.0 KB"
    mock_result.created_at = "2026-10-18T20:00:00Z"
    mock_result.error = None

    mock_extractor = MagicMock()
    mock_extractor.get_video_duration.return_value = 10.0
    mock_extractor.extract.return_value = mock_result

    with patch("api.routers.clips.get_clip_extractor", return_value=mock_extractor):
        res = client_tenant_a.post(
            "/api/v1/clips/extract",
            json={
                "source": "video_a.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:05",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["clip_id"] == "testcid123"
        # Verify output path is relative and does NOT leak server filesystem root
        assert data["output_path"].startswith("clips/")
        assert not os.path.isabs(data["output_path"])


# ===========================================================================
# 8. API Endpoints: Clip Merging Security Boundary
# ===========================================================================

def test_api_clip_merge_rejects_path_traversal(client_tenant_a):
    """POST /api/v1/clips/merge rejects traversal in clips list."""
    res = client_tenant_a.post(
        "/api/v1/clips/merge",
        json={
            "clips": ["../../etc/passwd", "clip_a1.mp4"],
            "output_format": "mp4",
        },
    )
    assert res.status_code in (400, 422)


def test_api_clip_merge_rejects_absolute_path(client_tenant_a):
    """POST /api/v1/clips/merge rejects absolute server paths."""
    res = client_tenant_a.post(
        "/api/v1/clips/merge",
        json={
            "clips": ["/etc/passwd", "/var/log/syslog"],
            "output_format": "mp4",
        },
    )
    assert res.status_code in (400, 422)


def test_api_clip_merge_cross_tenant_isolation(client_tenant_a, tenant_b_fixture):
    """Tenant A cannot merge clips belonging to Tenant B."""
    res = client_tenant_a.post(
        "/api/v1/clips/merge",
        json={
            "clips": ["clip_a1.mp4", "clip_b_private.mp4"],
            "output_format": "mp4",
        },
    )
    assert res.status_code in (404, 400)
    assert "not found in tenant storage" in res.json()["detail"].lower()


def test_api_clip_merge_input_limits(client_tenant_a):
    """POST /api/v1/clips/merge bounds input count (2 <= count <= 20)."""
    # Too few (< 2)
    res_few = client_tenant_a.post(
        "/api/v1/clips/merge",
        json={"clips": ["clip_a1.mp4"]},
    )
    assert res_few.status_code in (400, 422)

    # Too many (> 20)
    res_many = client_tenant_a.post(
        "/api/v1/clips/merge",
        json={"clips": [f"clip_{i}.mp4" for i in range(25)]},
    )
    assert res_many.status_code in (400, 422)


def test_api_clip_merge_legitimate(client_tenant_a, tenant_a_fixture):
    """POST /api/v1/clips/merge succeeds for authorized tenant clips and returns safe relative path."""
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.merge_id = "testmid123"
    mock_result.clip_count = 2
    mock_result.output_path = "/app/downloads/tenant-a-uuid/merged/merged_2clips.mp4"
    mock_result.total_duration_seconds = 10.0
    mock_result.file_size_bytes = 2048
    mock_result.file_size_human = "2.0 KB"
    mock_result.created_at = "2026-10-18T20:00:00Z"
    mock_result.clips_used = ["clip_a1.mp4", "clip_a2.mp4"]
    mock_result.error = None

    mock_merger = MagicMock()
    mock_merger.merge.return_value = mock_result

    with patch("api.routers.clips_merge.get_clip_merger", return_value=mock_merger):
        res = client_tenant_a.post(
            "/api/v1/clips/merge",
            json={
                "clips": ["clip_a1.mp4", "clip_a2.mp4"],
                "output_format": "mp4",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["merge_id"] == "testmid123"
        # Output path is relative
        assert data["output_path"].startswith("merged/")
        assert not os.path.isabs(data["output_path"])


# ===========================================================================
# 9. Artifact Download Authorization & Cross-Tenant Boundary
# ===========================================================================

def test_download_clip_cross_tenant_denied(client_tenant_a, tenant_b_fixture):
    """Tenant A cannot download a clip generated by Tenant B."""
    clip_b = tenant_b_fixture["clip"]
    clip_b_id = "private"  # in filename clip_b_private.mp4

    res = client_tenant_a.get(f"/api/v1/clips/{clip_b_id}/download")
    assert res.status_code == 404


def test_download_clip_invalid_id_rejected(client_tenant_a):
    """GET /api/v1/clips/{clip_id}/download rejects path traversal in clip_id."""
    res = client_tenant_a.get("/api/v1/clips/..%2F..%2Fetc/download")
    assert res.status_code in (400, 404)


def test_download_merged_cross_tenant_denied(client_tenant_a, tenant_b_fixture):
    """Tenant A cannot download a merged video generated by Tenant B."""
    b_merged = get_tenant_media_dir(tenant_b_fixture["tenant_id"], "merged") / "merged_secret123_2clips.mp4"
    b_merged.write_bytes(b"\x00" * 100)

    res = client_tenant_a.get("/api/v1/clips/merge/secret123/download")
    assert res.status_code == 404


# ===========================================================================
# 10. API Key Scopes & RBAC Compatibility
# ===========================================================================

@pytest.mark.asyncio
async def test_api_key_insufficient_scope_for_clips(tenant_a_fixture):
    """API key without clips:write scope cannot extract or merge clips."""
    from api.dependencies import AuthMethod, require_scope
    scope_checker_write = require_scope("clips:write")
    scope_checker_read = require_scope("clips:read")

    user_without_scope = CurrentUser(
        user_id="key-user",
        email="key@example.com",
        tenant_id=tenant_a_fixture["tenant_id"],
        role="member",
        scopes=["downloads:read"],
        auth_method=AuthMethod.API_KEY,
        permissions=["downloads:read"],
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await scope_checker_write(user=user_without_scope)
    assert exc_info.value.status_code == 403
    assert "insufficient api key permissions" in exc_info.value.detail.lower()

    with pytest.raises(HTTPException) as exc_info2:
        await scope_checker_read(user=user_without_scope)
    assert exc_info2.value.status_code == 403
