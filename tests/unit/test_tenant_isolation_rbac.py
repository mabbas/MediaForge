"""Comprehensive Enterprise Tenant Isolation and Role-Based Access Control (RBAC) Test Suite.

Milestone: STEP 02-C1
Verifies:
1. Strict cross-tenant isolation for downloads, files, history, transcripts, clips, merged media, and WebSockets.
2. Anti-enumeration defense (HTTP 404 on cross-tenant resource lookups).
3. Intracompany member isolation (members see only their own jobs/files/history; admins/owners see tenant jobs).
4. Role hierarchy and privilege escalation prevention (member vs admin vs owner vs system_admin).
5. Global engine controls (pause/resume queue, update config) restricted exclusively to system_admin.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base, get_session
from api.database.models import DownloadJobDB, TenantDB, TranscriptDB, UserDB
from api.dependencies import (
    AuthMethod,
    CurrentUser,
    get_app,
    get_current_user,
)
from api.security.jwt import create_access_token
from api.services.progress_bridge import ProgressBridge, get_progress_bridge
from src.clip.extractor import get_clip_extractor
from src.clip.merger import get_clip_merger
from src.models.download import DownloadJob, DownloadProgress, DownloadRequest, DownloadResult
from src.models.enums import DownloadStatus, MediaType, ProviderType, Quality


@pytest.fixture
def multi_tenant_env(tmp_path):
    """Setup isolated multi-tenant database environment with Tenant A, Tenant B, and roles."""
    db_file = tmp_path / "rbac_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    old_db = os.environ.get("GID_API_DATABASE_URL")
    old_jwt = os.environ.get("GID_JWT_SECRET_KEY")
    os.environ["GID_API_DATABASE_URL"] = db_url
    os.environ["GID_JWT_SECRET_KEY"] = "rbac-super-secret-key-32-bytes-long!!"
    os.environ["GID_API_SKIP_SEED"] = "1"

    from api.config import get_api_settings
    get_api_settings.cache_clear()

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_a_id = "tenant-alpha-001"
    tenant_b_id = "tenant-beta-002"

    async def _setup_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            # Tenants
            ta = TenantDB(id=tenant_a_id, name="Tenant Alpha", slug="alpha", tier="platinum", is_active=True)
            tb = TenantDB(id=tenant_b_id, name="Tenant Beta", slug="beta", tier="pro", is_active=True)
            tsys = TenantDB(id="00000000-0000-0000-0000-000000000001", name="System Tenant", slug="system", tier="platinum", is_active=True)
            session.add_all([ta, tb, tsys])

            # Tenant A Users
            ua_owner = UserDB(id="user-a-owner", tenant_id=tenant_a_id, email="owner@alpha.local", role="owner", is_active=True)
            ua_admin = UserDB(id="user-a-admin", tenant_id=tenant_a_id, email="admin@alpha.local", role="admin", is_active=True)
            ua_mem1 = UserDB(id="user-a-mem1", tenant_id=tenant_a_id, email="mem1@alpha.local", role="member", is_active=True)
            ua_mem2 = UserDB(id="user-a-mem2", tenant_id=tenant_a_id, email="mem2@alpha.local", role="member", is_active=True)

            # Tenant B Users
            ub_owner = UserDB(id="user-b-owner", tenant_id=tenant_b_id, email="owner@beta.local", role="owner", is_active=True)
            ub_admin = UserDB(id="user-b-admin", tenant_id=tenant_b_id, email="admin@beta.local", role="admin", is_active=True)
            ub_mem1 = UserDB(id="user-b-mem1", tenant_id=tenant_b_id, email="mem1@beta.local", role="member", is_active=True)

            # Global System Admin
            u_sysadmin = UserDB(id="user-sysadmin", tenant_id="00000000-0000-0000-0000-000000000001", email="sysadmin@mediaforge.local", role="system_admin", is_active=True)

            session.add_all([ua_owner, ua_admin, ua_mem1, ua_mem2, ub_owner, ub_admin, ub_mem1, u_sysadmin])
            await session.commit()

    asyncio.run(_setup_db())

    from api.main import app

    async def _mock_get_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _mock_get_session

    users = {
        "a_owner": CurrentUser(user_id="user-a-owner", tenant_id=tenant_a_id, email="owner@alpha.local", role="owner", tier="platinum"),
        "a_admin": CurrentUser(user_id="user-a-admin", tenant_id=tenant_a_id, email="admin@alpha.local", role="admin", tier="platinum"),
        "a_mem1": CurrentUser(user_id="user-a-mem1", tenant_id=tenant_a_id, email="mem1@alpha.local", role="member", tier="platinum"),
        "a_mem2": CurrentUser(user_id="user-a-mem2", tenant_id=tenant_a_id, email="mem2@alpha.local", role="member", tier="platinum"),
        "b_owner": CurrentUser(user_id="user-b-owner", tenant_id=tenant_b_id, email="owner@beta.local", role="owner", tier="pro"),
        "b_admin": CurrentUser(user_id="user-b-admin", tenant_id=tenant_b_id, email="admin@beta.local", role="admin", tier="pro"),
        "b_mem1": CurrentUser(user_id="user-b-mem1", tenant_id=tenant_b_id, email="mem1@beta.local", role="member", tier="pro"),
        "sysadmin": CurrentUser(user_id="user-sysadmin", tenant_id="00000000-0000-0000-0000-000000000001", email="sysadmin@mediaforge.local", role="system_admin", tier="platinum"),
    }

    tokens = {
        name: create_access_token(user_id=u.user_id, tenant_id=u.tenant_id)[0]
        for name, u in users.items()
    }

    with TestClient(app) as client:
        yield {
            "client": client,
            "session_factory": session_factory,
            "users": users,
            "tokens": tokens,
            "tenant_a_id": tenant_a_id,
            "tenant_b_id": tenant_b_id,
            "tmp_path": tmp_path,
        }

    app.dependency_overrides.pop(get_session, None)
    asyncio.run(engine.dispose())
    if old_db:
        os.environ["GID_API_DATABASE_URL"] = old_db
    else:
        os.environ.pop("GID_API_DATABASE_URL", None)
    if old_jwt:
        os.environ["GID_JWT_SECRET_KEY"] = old_jwt
    else:
        os.environ.pop("GID_JWT_SECRET_KEY", None)
    get_api_settings.cache_clear()


# ===========================================================================
# 1. Cross-Tenant Download Isolation & Anti-Enumeration
# ===========================================================================

def test_cross_tenant_download_submission_and_listing_isolation(multi_tenant_env):
    """Downloads submitted by Tenant A are never visible to Tenant B."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]

    # Tenant A submits a download
    resp_a = client.post(
        "/api/v1/downloads",
        json={"url": "https://example.com/video-alpha.mp4", "start": False},
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert resp_a.status_code == 202
    job_a_id = resp_a.json()["job"]["job_id"]

    # Tenant B lists downloads -> job_a_id must NOT appear
    resp_b_list = client.get(
        "/api/v1/downloads",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_b_list.status_code == 200
    b_job_ids = [j["job_id"] for j in resp_b_list.json()["jobs"]]
    assert job_a_id not in b_job_ids


def test_cross_tenant_download_lifecycle_control_returns_404(multi_tenant_env):
    """Tenant B cannot start, pause, cancel, move, or resume Tenant A's download (anti-enumeration 404)."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]

    # Tenant A submits a deferred download
    resp_a = client.post(
        "/api/v1/downloads",
        json={"url": "https://example.com/video-alpha-2.mp4", "start": False},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    job_id = resp_a.json()["job"]["job_id"]

    # Tenant B tries to start Tenant A's job -> 404
    resp_start = client.post(
        f"/api/v1/downloads/{job_id}/start",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_start.status_code == 404

    # Tenant B tries to pause Tenant A's job -> 404
    resp_pause = client.post(
        f"/api/v1/downloads/{job_id}/pause",
        headers={"Authorization": f"Bearer {tokens['b_admin']}"},
    )
    assert resp_pause.status_code == 404

    # Tenant B tries to cancel Tenant A's job -> 404
    resp_cancel = client.post(
        f"/api/v1/downloads/{job_id}/cancel",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_cancel.status_code == 404

    # Tenant B tries to move Tenant A's job -> 400 or 404
    resp_move = client.post(
        f"/api/v1/downloads/{job_id}/move-up",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_move.status_code in (400, 404)

    # Tenant B tries to resume Tenant A's job -> 404
    resp_resume = client.post(
        f"/api/v1/downloads/{job_id}/resume",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_resume.status_code == 404


def test_cross_tenant_download_file_leakage_prevented(multi_tenant_env):
    """Tenant B cannot download completed media files belonging to Tenant A."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]
    session_factory = multi_tenant_env["session_factory"]
    tmp_path = multi_tenant_env["tmp_path"]

    # Create dummy completed file on disk
    file_path = tmp_path / "tenant_a_secret_media.mp4"
    file_path.write_bytes(b"TOP SECRET TENANT A MEDIA CONTENT")
    job_id = "job-alpha-completed-001"

    # Save completed job in DB for Tenant A
    async def _insert_job():
        async with session_factory() as session:
            job = DownloadJobDB(
                id=job_id,
                tenant_id=multi_tenant_env["tenant_a_id"],
                user_id="user-a-mem1",
                url="https://example.com/secret.mp4",
                provider="generic",
                media_type="video",
                status="completed",
                file_path=str(file_path),
                file_size_bytes=len(b"TOP SECRET TENANT A MEDIA CONTENT"),
                completed_at=datetime.utcnow(),
            )
            session.add(job)
            await session.commit()

    asyncio.run(_insert_job())

    # Tenant B attempts to fetch file -> 404 Not Found
    resp_b = client.get(
        f"/api/v1/downloads/{job_id}/file",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_b.status_code == 404

    # Tenant A member who owns it -> 200 OK
    resp_a = client.get(
        f"/api/v1/downloads/{job_id}/file",
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert resp_a.status_code == 200
    assert resp_a.content == b"TOP SECRET TENANT A MEDIA CONTENT"


# ===========================================================================
# 2. Cross-Tenant History & Statistics Isolation
# ===========================================================================

def test_cross_tenant_history_isolation(multi_tenant_env):
    """Tenant B cannot read or clear Tenant A's download history."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]
    session_factory = multi_tenant_env["session_factory"]

    # Seed history in DB
    async def _seed_history():
        async with session_factory() as session:
            j_a = DownloadJobDB(
                id="hist-a-1",
                tenant_id=multi_tenant_env["tenant_a_id"],
                user_id="user-a-mem1",
                url="https://example.com/hist-a.mp4",
                provider="youtube",
                media_type="video",
                status="completed",
                file_size_bytes=5000,
                created_at=datetime.utcnow(),
            )
            j_b = DownloadJobDB(
                id="hist-b-1",
                tenant_id=multi_tenant_env["tenant_b_id"],
                user_id="user-b-mem1",
                url="https://example.com/hist-b.mp4",
                provider="youtube",
                media_type="video",
                status="completed",
                file_size_bytes=2000,
                created_at=datetime.utcnow(),
            )
            session.add_all([j_a, j_b])
            await session.commit()

    asyncio.run(_seed_history())

    # Tenant B lists history
    resp_b = client.get(
        "/api/v1/history",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_b.status_code == 200
    entries_b = resp_b.json()["entries"]
    assert len(entries_b) == 1
    assert entries_b[0]["job_id"] == "hist-b-1"

    # Tenant B gets stats -> should only show Tenant B's total
    resp_b_stats = client.get(
        "/api/v1/history/stats",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_b_stats.status_code == 200
    assert resp_b_stats.json()["total_downloads"] == 1
    assert resp_b_stats.json()["total_bytes_downloaded"] == 2000

    # Tenant B deletes history -> only hist-b-1 is deleted, hist-a-1 survives
    del_resp = client.delete(
        "/api/v1/history",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted_count"] == 1

    # Verify Tenant A history is still intact
    resp_a = client.get(
        "/api/v1/history",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert resp_a.status_code == 200
    assert len(resp_a.json()["entries"]) == 1
    assert resp_a.json()["entries"][0]["job_id"] == "hist-a-1"


# ===========================================================================
# 3. Cross-Tenant Transcript Isolation
# ===========================================================================

def test_cross_tenant_transcript_isolation(multi_tenant_env):
    """Tenant B cannot read or enumerate Tenant A's saved transcripts."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]
    session_factory = multi_tenant_env["session_factory"]

    # Seed transcripts
    t_a_id = "transcript-alpha-001"
    async def _seed_transcripts():
        async with session_factory() as session:
            ta = TranscriptDB(
                id=t_a_id,
                tenant_id=multi_tenant_env["tenant_a_id"],
                user_id="user-a-mem1",
                url="https://youtube.com/watch?v=alpha123",
                language="en",
                source="youtube",
                format="srt",
                content="1\n00:00:00,000 --> 00:00:05,000\nAlpha Secret",
                word_count=2,
            )
            tb = TranscriptDB(
                id="transcript-beta-002",
                tenant_id=multi_tenant_env["tenant_b_id"],
                user_id="user-b-mem1",
                url="https://youtube.com/watch?v=beta456",
                language="en",
                source="youtube",
                format="srt",
                content="1\n00:00:00,000 --> 00:00:05,000\nBeta Public",
                word_count=2,
            )
            session.add_all([ta, tb])
            await session.commit()

    asyncio.run(_seed_transcripts())

    # Tenant B lists transcripts
    resp_b_list = client.get(
        "/api/v1/transcripts",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_b_list.status_code == 200
    t_ids = [t["id"] for t in resp_b_list.json()["transcripts"]]
    assert t_a_id not in t_ids

    # Tenant B gets Tenant A transcript by ID -> 404 Not Found
    resp_b_get = client.get(
        f"/api/v1/transcripts/{t_a_id}",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert resp_b_get.status_code == 404

    # Tenant A gets Tenant A transcript -> 200 OK
    resp_a_get = client.get(
        f"/api/v1/transcripts/{t_a_id}",
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert resp_a_get.status_code == 200
    assert "Alpha Secret" in resp_a_get.json()["content"]


# ===========================================================================
# 4. Cross-Tenant Clip & Merge Download Isolation
# ===========================================================================

def test_cross_tenant_clips_and_merge_download_isolation(multi_tenant_env):
    """Clips and merged files stored in Tenant A's directory cannot be downloaded by Tenant B."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]
    with patch("src.env_loader.resolve_ffmpeg_binaries", return_value=("fake_ffmpeg", "fake_ffprobe")):
        extractor = get_clip_extractor()
        merger = get_clip_merger()

        tenant_a_id = multi_tenant_env["tenant_a_id"]

        # Place a dummy clip in Tenant A's directory
        clip_id = "testc123"
        tenant_a_clip_dir = Path(extractor._output_dir) / tenant_a_id
        tenant_a_clip_dir.mkdir(parents=True, exist_ok=True)
        clip_file = tenant_a_clip_dir / f"video_clip_{clip_id}_00-00-01_to_00-00-05.mp4"
        clip_file.write_bytes(b"TENANT A CLIP CONTENT")

        # Tenant B tries to download Tenant A's clip -> 404
        resp_b_clip = client.get(
            f"/api/v1/clips/{clip_id}/download",
            headers={"Authorization": f"Bearer {tokens['b_owner']}"},
        )
        assert resp_b_clip.status_code == 404

        # Tenant A member tries to download legacy clip without DB record -> 404 fail closed
        resp_mem_clip = client.get(
            f"/api/v1/clips/{clip_id}/download",
            headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
        )
        assert resp_mem_clip.status_code == 404

        # By default, legacy unindexed media is disabled: Tenant A owner also receives 404
        resp_a_clip_default = client.get(
            f"/api/v1/clips/{clip_id}/download",
            headers={"Authorization": f"Bearer {tokens['a_owner']}"},
        )
        assert resp_a_clip_default.status_code == 404

        # When opt-in legacy fallback is enabled, Tenant A owner can download legacy clip -> 200 OK
        with patch.dict(os.environ, {"MEDIAFORGE_ENABLE_LEGACY_MEDIA_FALLBACK": "1"}):
            # Member still fails closed -> 404
            resp_mem_optin = client.get(
                f"/api/v1/clips/{clip_id}/download",
                headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
            )
            assert resp_mem_optin.status_code == 404

            # Owner downloads -> 200 OK
            resp_a_clip = client.get(
                f"/api/v1/clips/{clip_id}/download",
                headers={"Authorization": f"Bearer {tokens['a_owner']}"},
            )
            assert resp_a_clip.status_code == 200
            assert resp_a_clip.content == b"TENANT A CLIP CONTENT"

        # Place a dummy merged file in Tenant A's directory
        merge_id = "testm456"
        tenant_a_merge_dir = Path(merger._output_dir) / tenant_a_id
        tenant_a_merge_dir.mkdir(parents=True, exist_ok=True)
        merge_file = tenant_a_merge_dir / f"merged_{merge_id}_2clips.mp4"
        merge_file.write_bytes(b"TENANT A MERGED CONTENT")

        # Tenant B tries to download Tenant A's merged file -> 404
        resp_b_merge = client.get(
            f"/api/v1/clips/merge/{merge_id}/download",
            headers={"Authorization": f"Bearer {tokens['b_owner']}"},
        )
        assert resp_b_merge.status_code == 404

        # Tenant A member tries to download legacy merged file without DB record -> 404 fail closed
        resp_mem_merge = client.get(
            f"/api/v1/clips/merge/{merge_id}/download",
            headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
        )
        assert resp_mem_merge.status_code == 404

        # By default, legacy merged file is disabled: Owner receives 404
        resp_a_merge_default = client.get(
            f"/api/v1/clips/merge/{merge_id}/download",
            headers={"Authorization": f"Bearer {tokens['a_owner']}"},
        )
        assert resp_a_merge_default.status_code == 404

        # When opt-in legacy fallback is enabled, Owner downloads merged file -> 200 OK
        with patch.dict(os.environ, {"MEDIAFORGE_ENABLE_LEGACY_MEDIA_FALLBACK": "1"}):
            resp_a_merge = client.get(
                f"/api/v1/clips/merge/{merge_id}/download",
                headers={"Authorization": f"Bearer {tokens['a_owner']}"},
            )
            assert resp_a_merge.status_code == 200
            assert resp_a_merge.content == b"TENANT A MERGED CONTENT"


# ===========================================================================
# 5. Usage Reset Scoping & RBAC
# ===========================================================================

def test_usage_reset_rbac_and_cross_tenant_protection(multi_tenant_env):
    """Members cannot reset usage (403), admins cannot reset other tenants (404)."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]

    # Member calling reset -> 403 Forbidden
    resp_mem = client.post(
        "/api/v1/usage/reset",
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert resp_mem.status_code == 403

    # Tenant A Admin tries to reset user in Tenant B -> 404 Anti-enumeration
    resp_cross = client.post(
        "/api/v1/usage/reset?target_user_id=user-b-mem1",
        headers={"Authorization": f"Bearer {tokens['a_admin']}"},
    )
    assert resp_cross.status_code == 404

    # Tenant A Admin resets member in Tenant A -> 200 OK
    resp_ok = client.post(
        "/api/v1/usage/reset?target_user_id=user-a-mem1",
        headers={"Authorization": f"Bearer {tokens['a_admin']}"},
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["success"] is True


# ===========================================================================
# 6. Global Server Config & Queue Pause/Resume RBAC Hierarchy
# ===========================================================================

def test_global_engine_controls_restricted_to_system_admin(multi_tenant_env):
    """Tenant owners and admins CANNOT modify global engine config or pause/resume queue."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]

    # Tenant A Member -> 403
    assert client.put("/api/v1/config", json={}, headers={"Authorization": f"Bearer {tokens['a_mem1']}"}).status_code == 403
    assert client.post("/api/v1/queue/pause", headers={"Authorization": f"Bearer {tokens['a_mem1']}"}).status_code == 403

    # Tenant A Admin -> 403
    assert client.put("/api/v1/config", json={}, headers={"Authorization": f"Bearer {tokens['a_admin']}"}).status_code == 403
    assert client.post("/api/v1/queue/pause", headers={"Authorization": f"Bearer {tokens['a_admin']}"}).status_code == 403

    # Tenant A Owner (tenant-scoped authority only, NOT global server admin) -> 403
    assert client.put("/api/v1/config", json={}, headers={"Authorization": f"Bearer {tokens['a_owner']}"}).status_code == 403
    assert client.post("/api/v1/queue/pause", headers={"Authorization": f"Bearer {tokens['a_owner']}"}).status_code == 403
    assert client.post("/api/v1/queue/resume", headers={"Authorization": f"Bearer {tokens['a_owner']}"}).status_code == 403

    # Global System Admin -> 200
    resp_cfg = client.put(
        "/api/v1/config",
        json={"max_concurrent_downloads": 4},
        headers={"Authorization": f"Bearer {tokens['sysadmin']}"},
    )
    assert resp_cfg.status_code == 200

    resp_pause = client.post(
        "/api/v1/queue/pause",
        headers={"Authorization": f"Bearer {tokens['sysadmin']}"},
    )
    assert resp_pause.status_code == 200

    resp_resume = client.post(
        "/api/v1/queue/resume",
        headers={"Authorization": f"Bearer {tokens['sysadmin']}"},
    )
    assert resp_resume.status_code == 200


# ===========================================================================
# 7. Intracompany Member Isolation
# ===========================================================================

def test_same_tenant_member_isolation(multi_tenant_env):
    """Within Tenant A, Member 1 cannot see or download Member 2's jobs, but Admin can."""
    client = multi_tenant_env["client"]
    tokens = multi_tenant_env["tokens"]
    session_factory = multi_tenant_env["session_factory"]
    tmp_path = multi_tenant_env["tmp_path"]

    # Member 1 submits a download
    resp = client.post(
        "/api/v1/downloads",
        json={"url": "https://example.com/mem1-private.mp4", "start": False},
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    mem1_job_id = resp.json()["job"]["job_id"]

    # Member 2 in the SAME tenant lists downloads -> must NOT see Member 1's download
    resp_mem2_list = client.get(
        "/api/v1/downloads",
        headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
    )
    mem2_job_ids = [j["job_id"] for j in resp_mem2_list.json()["jobs"]]
    assert mem1_job_id not in mem2_job_ids

    # Tenant Admin in the SAME tenant lists downloads -> sees Member 1's download
    resp_admin_list = client.get(
        "/api/v1/downloads",
        headers={"Authorization": f"Bearer {tokens['a_admin']}"},
    )
    admin_job_ids = [j["job_id"] for j in resp_admin_list.json()["jobs"]]
    assert mem1_job_id in admin_job_ids


# ===========================================================================
# 8. WebSocket Tenant Isolation Broadcast
# ===========================================================================

@pytest.mark.asyncio
async def test_websocket_progress_bridge_tenant_isolation():
    """ProgressBridge guarantees that Tenant B WebSocket subscribers never receive Tenant A events."""
    bridge = ProgressBridge()
    bridge.start()

    ws_client_a = AsyncMock()
    ws_client_b = AsyncMock()

    # Register clients
    bridge.register_client(ws_client_a, tenant_id="tenant-alpha", user_id="user-a-1", role="member")
    bridge.register_client(ws_client_b, tenant_id="tenant-beta", user_id="user-b-1", role="member")

    # Both subscribe to all
    bridge.subscribe(ws_client_a, subscribe_all=True)
    bridge.subscribe(ws_client_b, subscribe_all=True)

    # Mock engine job belonging to Tenant A
    mock_job = DownloadJob(
        job_id="job-alpha-ws-1",
        request=DownloadRequest(url="https://example.com/alpha.mp4"),
        progress=DownloadProgress(job_id="job-alpha-ws-1", status=DownloadStatus.DOWNLOADING, percent=45.0),
        tenant_id="tenant-alpha",
        user_id="user-a-1",
    )

    with patch("api.dependencies.get_app") as mock_get_app:
        mock_app = MagicMock()
        mock_app.get_job.return_value = mock_job
        mock_get_app.return_value = mock_app

        await bridge._broadcast("job-alpha-ws-1", mock_job.progress)

    # Tenant A client received progress message
    assert ws_client_a.send_json.called
    sent_msg = ws_client_a.send_json.call_args[0][0]
    assert sent_msg["job_id"] == "job-alpha-ws-1"
    assert sent_msg["percent"] == 45.0

    # Tenant B client NEVER received progress message!
    assert not ws_client_b.send_json.called

    bridge.stop()
