"""Comprehensive persistent media artifacts, ownership, and lifecycle test suite (STEP 02-E1).

Verifies:
1. Database-backed identity and persistence for extracted clips and merged videos.
2. Intra-tenant member isolation (Member A cannot view, download, or delete Member B's clips).
3. Tenant Admin / Owner oversight (Admins can view and download all artifacts within their tenant).
4. Cross-tenant anti-enumeration (404 on cross-tenant artifact requests).
5. Decoupled FFmpeg lifecycle and state transitions (processing -> ready / failed).
6. Artifact status enforcement (non-ready artifacts return 400).
7. Concat/Merge input ownership verification.
8. Soft deletion and deterministic physical file removal.
9. API key attribution and scope enforcement (clips:write, clips:read).
10. Error message path sanitization.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base, get_session
from api.database.models import APIKeyDB, MediaArtifactDB, TenantDB, UserDB
from api.dependencies import CurrentUser
from api.security.jwt import create_access_token
from api.security.media_storage import (
    get_media_storage_root,
    get_tenant_media_dir,
    get_tenant_storage_root,
)


@pytest.fixture
def artifacts_env(tmp_path, monkeypatch):
    """Set up isolated multi-tenant database and filesystem environment."""
    storage_root = tmp_path / "media_storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GID_DOWNLOAD__OUTPUT_DIRECTORY", str(storage_root))

    db_file = tmp_path / "artifacts_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("GID_API_DATABASE_URL", db_url)
    monkeypatch.setenv("GID_JWT_SECRET_KEY", "artifacts-test-secret-key-at-least-32-bytes!!")
    monkeypatch.setenv("GID_API_SKIP_SEED", "1")

    from api.config import get_api_settings
    get_api_settings.cache_clear()

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Decoupled fresh session factory patch
    import api.services.artifact_service as art_svc
    monkeypatch.setattr(art_svc, "async_session_factory", session_factory)

    tenant_a_id = "tenant-alpha"
    tenant_b_id = "tenant-beta"

    async def _setup_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            ta = TenantDB(id=tenant_a_id, name="Alpha Corp", slug="alpha", tier="platinum", is_active=True)
            tb = TenantDB(id=tenant_b_id, name="Beta Corp", slug="beta", tier="pro", is_active=True)
            session.add_all([ta, tb])

            ua_owner = UserDB(id="user-a-owner", tenant_id=tenant_a_id, email="owner@alpha.test", role="owner", is_active=True)
            ua_admin = UserDB(id="user-a-admin", tenant_id=tenant_a_id, email="admin@alpha.test", role="admin", is_active=True)
            ua_mem1 = UserDB(id="user-a-mem1", tenant_id=tenant_a_id, email="mem1@alpha.test", role="member", is_active=True)
            ua_mem2 = UserDB(id="user-a-mem2", tenant_id=tenant_a_id, email="mem2@alpha.test", role="member", is_active=True)

            ub_owner = UserDB(id="user-b-owner", tenant_id=tenant_b_id, email="owner@beta.test", role="owner", is_active=True)
            ub_mem1 = UserDB(id="user-b-mem1", tenant_id=tenant_b_id, email="mem1@beta.test", role="member", is_active=True)

            # API Key for Member 1
            key1 = APIKeyDB(
                id="key-a-mem1",
                name="Mem1 Key",
                key_prefix="mf_live_test",
                key_hash="hash_mem1_key",
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                permissions=["clips:write", "clips:read"],
                is_active=True,
                is_revoked=False,
            )
            session.add_all([ua_owner, ua_admin, ua_mem1, ua_mem2, ub_owner, ub_mem1, key1])
            await session.commit()

    asyncio.run(_setup_db())

    # Create dummy source videos in tenant storage
    a_downloads = get_tenant_media_dir(tenant_a_id, "downloads")
    src_video_a = a_downloads / "source_video.mp4"
    src_video_a.write_bytes(b"\x00" * 4096)

    b_downloads = get_tenant_media_dir(tenant_b_id, "downloads")
    src_video_b = b_downloads / "secret_beta.mp4"
    src_video_b.write_bytes(b"\x00" * 4096)

    async def _seed_source_jobs():
        from api.database.models import DownloadJobDB
        async with session_factory() as session:
            job1 = DownloadJobDB(
                id="job-source-01",
                url="https://youtube.com/watch?v=source123",
                provider="youtube",
                media_type="video",
                status="completed",
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                file_path="downloads/source_video.mp4",
                title="source_video.mp4",
            )
            session.add(job1)
            await session.commit()

    asyncio.run(_seed_source_jobs())

    from api.main import app

    async def _mock_get_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _mock_get_session

    users = {
        "a_owner": CurrentUser(user_id="user-a-owner", tenant_id=tenant_a_id, email="owner@alpha.test", role="owner", tier="platinum"),
        "a_admin": CurrentUser(user_id="user-a-admin", tenant_id=tenant_a_id, email="admin@alpha.test", role="admin", tier="platinum"),
        "a_mem1": CurrentUser(user_id="user-a-mem1", tenant_id=tenant_a_id, email="mem1@alpha.test", role="member", tier="platinum"),
        "a_mem2": CurrentUser(user_id="user-a-mem2", tenant_id=tenant_a_id, email="mem2@alpha.test", role="member", tier="platinum"),
        "b_owner": CurrentUser(user_id="user-b-owner", tenant_id=tenant_b_id, email="owner@beta.test", role="owner", tier="pro"),
        "b_mem1": CurrentUser(user_id="user-b-mem1", tenant_id=tenant_b_id, email="mem1@beta.test", role="member", tier="pro"),
    }

    tokens = {
        name: create_access_token(user_id=u.user_id, tenant_id=u.tenant_id, extra_claims={"role": u.role})[0]
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
            "storage_root": storage_root,
        }

    app.dependency_overrides.pop(get_session, None)
    asyncio.run(engine.dispose())
    get_api_settings.cache_clear()


def test_artifact_lifecycle_clip_extraction(artifacts_env):
    """Clip extraction creates persistent MediaArtifactDB record with decoupled lifecycle."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]
    tenant_a_id = artifacts_env["tenant_a_id"]
    session_factory = artifacts_env["session_factory"]

    # Mock FFmpeg extraction
    mock_res = MagicMock()
    mock_res.success = True
    mock_res.clip_id = "testclip101"
    mock_res.start_time = "00:00:01"
    mock_res.end_time = "00:00:03"
    mock_res.duration_seconds = 2.0
    mock_res.file_size_bytes = 1024
    mock_res.file_size_human = "1.0 KB"
    mock_res.created_at = "2026-10-18T22:00:00Z"
    mock_res.error = None

    def fake_extract(clip_req):
        p = Path(clip_req.output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"SYNTHETIC CLIP BY MEM1")
        mock_res.clip_id = clip_req.clip_id
        return mock_res

    mock_extractor = MagicMock()
    mock_extractor.extract.side_effect = fake_extract
    mock_extractor.get_video_duration.return_value = 60.0

    with patch("api.routers.clips.get_clip_extractor", return_value=mock_extractor):
        resp = client.post(
            "/api/v1/clips/extract",
            json={
                "source": "source_video.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:03",
                "output_format": "mp4",
            },
            headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        cid = data["clip_id"]

    # Verify database record
    async def _verify_db():
        async with session_factory() as s:
            stmt = select(MediaArtifactDB).where(MediaArtifactDB.id == cid)
            art = (await s.execute(stmt)).scalar_one_or_none()
            assert art is not None
            assert art.tenant_id == tenant_a_id
            assert art.user_id == "user-a-mem1"
            assert art.artifact_type == "clip"
            assert art.status == "ready"
            assert art.file_size_bytes > 0
            assert art.sha256_checksum is not None

    asyncio.run(_verify_db())


def test_intra_tenant_member_isolation(artifacts_env):
    """Member B in same tenant CANNOT access, download, or delete Member A's clip."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]
    tenant_a_id = artifacts_env["tenant_a_id"]
    session_factory = artifacts_env["session_factory"]

    # Seed clip owned by Member 1
    clip_id = "clip-mem1-private"
    clips_dir = get_tenant_media_dir(tenant_a_id, "clips")
    clip_file = clips_dir / f"clip_{clip_id}.mp4"
    clip_file.write_bytes(b"SECRET CONTENT OF MEMBER 1")

    async def _seed_art():
        async with session_factory() as s:
            art = MediaArtifactDB(
                id=clip_id,
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                artifact_type="clip",
                storage_path=f"clips/{clip_file.name}",
                filename=clip_file.name,
                format="mp4",
                file_size_bytes=len(b"SECRET CONTENT OF MEMBER 1"),
                status="ready",
            )
            s.add(art)
            await s.commit()

    asyncio.run(_seed_art())

    # 1. Member 1 downloads -> 200 OK
    res_m1 = client.get(
        f"/api/v1/clips/{clip_id}/download",
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert res_m1.status_code == 200
    assert res_m1.content == b"SECRET CONTENT OF MEMBER 1"

    # 2. Member 2 (same tenant) attempts download -> 404 Anti-enumeration
    res_m2 = client.get(
        f"/api/v1/clips/{clip_id}/download",
        headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
    )
    assert res_m2.status_code == 404

    # 3. Member 2 attempts dedicated artifact download -> 404
    res_m2_art = client.get(
        f"/api/v1/artifacts/{clip_id}/download",
        headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
    )
    assert res_m2_art.status_code == 404

    # 4. Member 2 attempts metadata inspection -> 404
    res_m2_meta = client.get(
        f"/api/v1/artifacts/{clip_id}",
        headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
    )
    assert res_m2_meta.status_code == 404

    # 5. Member 2 lists artifacts -> sees 0 items
    res_m2_list = client.get(
        "/api/v1/artifacts",
        headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
    )
    assert res_m2_list.status_code == 200
    assert len(res_m2_list.json()["items"]) == 0

    # 6. Member 2 attempts deletion -> 404
    res_m2_del = client.delete(
        f"/api/v1/artifacts/{clip_id}",
        headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
    )
    assert res_m2_del.status_code == 404


def test_tenant_admin_and_owner_oversight(artifacts_env):
    """Tenant Admin and Owner can view, download, and delete artifacts across the tenant."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]
    tenant_a_id = artifacts_env["tenant_a_id"]
    session_factory = artifacts_env["session_factory"]

    clip_id = "clip-oversight-test"
    clips_dir = get_tenant_media_dir(tenant_a_id, "clips")
    clip_file = clips_dir / f"clip_{clip_id}.mp4"
    clip_file.write_bytes(b"MEMBER CLIP WITH ADMIN ACCESS")

    async def _seed_art():
        async with session_factory() as s:
            art = MediaArtifactDB(
                id=clip_id,
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                artifact_type="clip",
                storage_path=f"clips/{clip_file.name}",
                filename=clip_file.name,
                format="mp4",
                file_size_bytes=len(b"MEMBER CLIP WITH ADMIN ACCESS"),
                status="ready",
            )
            s.add(art)
            await s.commit()

    asyncio.run(_seed_art())

    # Tenant Owner downloads Member 1's clip -> 200 OK
    res_owner = client.get(
        f"/api/v1/clips/{clip_id}/download",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert res_owner.status_code == 200
    assert res_owner.content == b"MEMBER CLIP WITH ADMIN ACCESS"

    # Tenant Admin views metadata -> 200 OK
    res_admin = client.get(
        f"/api/v1/artifacts/{clip_id}",
        headers={"Authorization": f"Bearer {tokens['a_admin']}"},
    )
    assert res_admin.status_code == 200
    assert res_admin.json()["id"] == clip_id
    assert res_admin.json()["user_id"] == "user-a-mem1"

    # Tenant Admin lists artifacts -> includes Member 1's clip
    res_admin_list = client.get(
        "/api/v1/artifacts",
        headers={"Authorization": f"Bearer {tokens['a_admin']}"},
    )
    assert res_admin_list.status_code == 200
    assert any(item["id"] == clip_id for item in res_admin_list.json()["items"])


def test_cross_tenant_anti_enumeration(artifacts_env):
    """Tenant B receives 404 on all requests targeting Tenant A artifacts."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]
    tenant_a_id = artifacts_env["tenant_a_id"]
    session_factory = artifacts_env["session_factory"]

    clip_id = "clip-a-secret"
    clips_dir = get_tenant_media_dir(tenant_a_id, "clips")
    clip_file = clips_dir / f"clip_{clip_id}.mp4"
    clip_file.write_bytes(b"CONFIDENTIAL ALPHA CORP CLIP")

    async def _seed_art():
        async with session_factory() as s:
            art = MediaArtifactDB(
                id=clip_id,
                tenant_id=tenant_a_id,
                user_id="user-a-owner",
                artifact_type="clip",
                storage_path=f"clips/{clip_file.name}",
                filename=clip_file.name,
                format="mp4",
                file_size_bytes=len(b"CONFIDENTIAL ALPHA CORP CLIP"),
                status="ready",
            )
            s.add(art)
            await s.commit()

    asyncio.run(_seed_art())

    # Tenant B Owner attempts download -> 404
    res_b = client.get(
        f"/api/v1/clips/{clip_id}/download",
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    assert res_b.status_code == 404

    # Tenant B Member attempts artifact download -> 404
    res_b_art = client.get(
        f"/api/v1/artifacts/{clip_id}/download",
        headers={"Authorization": f"Bearer {tokens['b_mem1']}"},
    )
    assert res_b_art.status_code == 404

    # Tenant B Member attempts deletion -> 404
    res_b_del = client.delete(
        f"/api/v1/artifacts/{clip_id}",
        headers={"Authorization": f"Bearer {tokens['b_mem1']}"},
    )
    assert res_b_del.status_code == 404


def test_status_guards_prevent_download_of_unready_artifacts(artifacts_env):
    """Artifacts in processing or failed state return HTTP 400 when download attempted."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]
    tenant_a_id = artifacts_env["tenant_a_id"]
    session_factory = artifacts_env["session_factory"]

    async def _seed_unready():
        async with session_factory() as s:
            p = MediaArtifactDB(
                id="art-processing",
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                artifact_type="clip",
                storage_path="clips/processing.mp4",
                filename="processing.mp4",
                status="processing",
            )
            f = MediaArtifactDB(
                id="art-failed",
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                artifact_type="clip",
                storage_path="clips/failed.mp4",
                filename="failed.mp4",
                status="failed",
                error_message="Encode error occurred",
            )
            s.add_all([p, f])
            await s.commit()

    asyncio.run(_seed_unready())

    # Processing download -> 400
    res_proc = client.get(
        "/api/v1/artifacts/art-processing/download",
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert res_proc.status_code == 400
    assert "not ready" in res_proc.json()["detail"].lower()

    # Failed download -> 400
    res_fail = client.get(
        "/api/v1/artifacts/art-failed/download",
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert res_fail.status_code == 400
    assert "not ready" in res_fail.json()["detail"].lower()


def test_artifact_deletion_unlinks_disk_file(artifacts_env):
    """DELETE /api/v1/artifacts/{id} marks record deleted and removes file from disk."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]
    tenant_a_id = artifacts_env["tenant_a_id"]
    session_factory = artifacts_env["session_factory"]

    clips_dir = get_tenant_media_dir(tenant_a_id, "clips")
    del_file = clips_dir / "to_delete.mp4"
    del_file.write_bytes(b"DATA TO BE PURGED")

    async def _seed():
        async with session_factory() as s:
            art = MediaArtifactDB(
                id="art-del-01",
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                artifact_type="clip",
                storage_path=f"clips/{del_file.name}",
                filename=del_file.name,
                status="ready",
            )
            s.add(art)
            await s.commit()

    asyncio.run(_seed())
    assert del_file.exists()

    res = client.delete(
        "/api/v1/artifacts/art-del-01",
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert res.status_code == 200
    assert res.json()["success"] is True

    # Physical file is unlinked
    assert not del_file.exists()

    # Database status is 'deleted'
    async def _check_db():
        async with session_factory() as s:
            art = (await s.execute(select(MediaArtifactDB).where(MediaArtifactDB.id == "art-del-01"))).scalar_one()
            assert art.status == "deleted"

    asyncio.run(_check_db())


def test_merge_artifact_persistence_and_isolation(artifacts_env):
    """Merging clips creates persistent merge record with full ownership enforcement."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]
    tenant_a_id = artifacts_env["tenant_a_id"]
    session_factory = artifacts_env["session_factory"]

    # Member 1 creates 2 input clips
    clips_dir = get_tenant_media_dir(tenant_a_id, "clips")
    c1 = clips_dir / "c1.mp4"
    c2 = clips_dir / "c2.mp4"
    c1.write_bytes(b"CLIP 1 DATA")
    c2.write_bytes(b"CLIP 2 DATA")

    async def _seed_inputs():
        async with session_factory() as s:
            a1 = MediaArtifactDB(
                id="c1-id",
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                artifact_type="clip",
                storage_path=f"clips/{c1.name}",
                filename=c1.name,
                status="ready",
            )
            a2 = MediaArtifactDB(
                id="c2-id",
                tenant_id=tenant_a_id,
                user_id="user-a-mem1",
                artifact_type="clip",
                storage_path=f"clips/{c2.name}",
                filename=c2.name,
                status="ready",
            )
            s.add_all([a1, a2])
            await s.commit()

    asyncio.run(_seed_inputs())

    # Mock merger
    mock_res = MagicMock()
    mock_res.success = True
    mock_res.merge_id = "mid999"
    mock_res.clip_count = 2
    mock_res.total_duration_seconds = 12.0
    mock_res.file_size_bytes = 4096
    mock_res.file_size_human = "4.0 KB"
    mock_res.created_at = "2026-10-18T22:10:00Z"
    mock_res.clips_used = ["c1.mp4", "c2.mp4"]
    mock_res.error = None

    def fake_merge(merge_req):
        p = Path(merge_req.output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MERGED MEDIA OUTPUT")
        mock_res.merge_id = merge_req.merge_id
        return mock_res

    mock_merger = MagicMock()
    mock_merger.merge.side_effect = fake_merge

    with patch("api.routers.clips_merge.get_clip_merger", return_value=mock_merger):
        # Member 1 merges clips -> 200 OK
        resp = client.post(
            "/api/v1/clips/merge",
            json={"clips": ["clips/c1.mp4", "clips/c2.mp4"], "output_format": "mp4"},
            headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
        )
        assert resp.status_code == 200
        mid = resp.json()["merge_id"]

    # Member 1 downloads merged file -> 200 OK
    res_dl = client.get(
        f"/api/v1/clips/merge/{mid}/download",
        headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
    )
    assert res_dl.status_code == 200
    assert res_dl.content == b"MERGED MEDIA OUTPUT"

    # Member 2 attempts to download merged file -> 404 Anti-enumeration
    res_m2 = client.get(
        f"/api/v1/clips/merge/{mid}/download",
        headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
    )
    assert res_m2.status_code == 404

    # Member 2 attempts to merge Member 1's clips -> 404 Anti-enumeration
    with patch("api.routers.clips_merge.get_clip_merger", return_value=mock_merger):
        res_m2_merge = client.post(
            "/api/v1/clips/merge",
            json={"clips": ["clips/c1.mp4", "clips/c2.mp4"], "output_format": "mp4"},
            headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
        )
        assert res_m2_merge.status_code == 404


def test_member_cannot_extract_from_another_members_download(artifacts_env):
    """Member 2 cannot extract clips from a source video owned by Member 1."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]

    mock_extractor = MagicMock()
    mock_extractor.get_video_duration.return_value = 60.0

    with patch("api.routers.clips.get_clip_extractor", return_value=mock_extractor):
        res = client.post(
            "/api/v1/clips/extract",
            json={
                "source": "source_video.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:05",
                "output_format": "mp4",
            },
            headers={"Authorization": f"Bearer {tokens['a_mem2']}"},
        )
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()


def test_sanitized_error_handling_on_failure(artifacts_env):
    """Failed FFmpeg executions store sanitized errors in database without server paths or tokens."""
    client = artifacts_env["client"]
    tokens = artifacts_env["tokens"]
    session_factory = artifacts_env["session_factory"]

    mock_res = MagicMock()
    mock_res.success = False
    mock_res.error = "FFmpeg crashed opening /var/lib/mediaforge/data/secret/token.txt at line 5"

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = mock_res
    mock_extractor.get_video_duration.return_value = 60.0

    with patch("api.routers.clips.get_clip_extractor", return_value=mock_extractor):
        res = client.post(
            "/api/v1/clips/extract",
            json={
                "source": "source_video.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:03",
                "output_format": "mp4",
            },
            headers={"Authorization": f"Bearer {tokens['a_mem1']}"},
        )
        assert res.status_code == 500

    # Inspect database artifact
    async def _check_failed():
        async with session_factory() as s:
            stmt = select(MediaArtifactDB).where(
                MediaArtifactDB.user_id == "user-a-mem1",
                MediaArtifactDB.status == "failed"
            )
            art = (await s.execute(stmt)).scalars().first()
            assert art is not None
            assert "/var/lib/mediaforge" not in art.error_message
            assert "[path]" in art.error_message

    asyncio.run(_check_failed())


def test_api_key_scoping_and_ownership(artifacts_env):
    """API key requests record api_key_id on artifact and enforce scopes."""
    client = artifacts_env["client"]
    session_factory = artifacts_env["session_factory"]
    tenant_a_id = artifacts_env["tenant_a_id"]

    # CurrentUser authenticated via API key with only clips:write
    from api.dependencies import AuthMethod
    user_write_only = CurrentUser(
        user_id="user-a-mem1",
        tenant_id=tenant_a_id,
        email="mem1@alpha.test",
        role="service_account",
        permissions=["clips:write"],
        auth_method=AuthMethod.API_KEY,
        api_key_id="key-a-mem1",
    )

    from api.dependencies import get_current_user
    from api.main import app

    app.dependency_overrides[get_current_user] = lambda: user_write_only

    mock_res = MagicMock()
    mock_res.success = True
    mock_res.clip_id = "apikey-clip-01"
    mock_res.start_time = "00:00:01"
    mock_res.end_time = "00:00:02"
    mock_res.duration_seconds = 2.0
    mock_res.file_size_bytes = 100
    mock_res.file_size_human = "100 B"
    mock_res.created_at = "2026-10-18T22:00:00Z"
    mock_res.error = None

    def fake_extract(req):
        Path(req.output_path).write_bytes(b"API KEY CLIP")
        mock_res.clip_id = req.clip_id
        return mock_res

    mock_ext = MagicMock()
    mock_ext.extract.side_effect = fake_extract
    mock_ext.get_video_duration.return_value = 60.0

    with patch("api.routers.clips.get_clip_extractor", return_value=mock_ext):
        res = client.post(
            "/api/v1/clips/extract",
            json={
                "source": "source_video.mp4",
                "start_time": "00:00:01",
                "end_time": "00:00:02",
                "output_format": "mp4",
            },
        )
        assert res.status_code == 200
        cid = res.json()["clip_id"]

    # Verify api_key_id in DB
    async def _check_apikey_record():
        async with session_factory() as s:
            art = (await s.execute(select(MediaArtifactDB).where(MediaArtifactDB.id == cid))).scalar_one()
            assert art.api_key_id == "key-a-mem1"
            assert art.user_id == "user-a-mem1"

    asyncio.run(_check_apikey_record())

    # Write-only key attempts download -> 403 Forbidden (missing clips:read)
    res_dl_forbidden = client.get(f"/api/v1/clips/{cid}/download")
    assert res_dl_forbidden.status_code == 403

    # Switch to key with clips:read
    user_read_write = CurrentUser(
        user_id="user-a-mem1",
        tenant_id=tenant_a_id,
        email="mem1@alpha.test",
        role="service_account",
        permissions=["clips:write", "clips:read"],
        auth_method=AuthMethod.API_KEY,
        api_key_id="key-a-mem1",
    )
    app.dependency_overrides[get_current_user] = lambda: user_read_write

    res_dl_ok = client.get(f"/api/v1/clips/{cid}/download")
    assert res_dl_ok.status_code == 200
    assert res_dl_ok.content == b"API KEY CLIP"

    app.dependency_overrides.pop(get_current_user, None)
