"""Focused Media Isolation and Authorization Hardening Test Suite (STEP 03-E2).

Verifies:
1. Valid download ID access by owner.
2. Valid artifact ID access by owner.
3. Denial of access to another member's download ID (same tenant).
4. Denial of access to another member's artifact ID (same tenant).
5. Denial of access to another member's relative path (same tenant).
6. Denial of access to another member's absolute tenant container path.
7. Denial of cross-tenant media ID (Tenant B accessing Tenant A).
8. Denial of cross-tenant relative/absolute path.
9. Denial of symlinks escaping tenant storage.
10. Denial of directory traversal tokens ('..').
11. Permitted administrative oversight (Owner/Admin accessing member media).
12. API-key access and strict scope enforcement.
13. Worker replay prevention: worker revalidates source authorization at execution time and fails closed if ownership changed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import uuid
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base, get_session
from api.database.models import DownloadJobDB, MediaArtifactDB, MediaWorkItemDB, TenantDB, UserDB
from api.dependencies import AuthMethod, CurrentUser, get_current_user
from api.schemas.clips import ClipExtractRequest, ClipValidateRequest
from api.security.jwt import create_access_token
from api.security.media_storage import (
    MediaSecurityError,
    get_tenant_media_dir,
    get_tenant_storage_root,
    resolve_and_authorize_clip_source,
)
from api.services.artifact_service import (
    resolve_source_media_reference,
    verify_source_media_authorization,
)
from api.services.job_service import submit_clip_job, submit_merge_job


@pytest.fixture
def isolation_env(tmp_path):
    """Set up isolated multi-tenant environment with Tenant A and Tenant B."""
    db_file = tmp_path / "isolation_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    old_db = os.environ.get("GID_API_DATABASE_URL")
    old_jwt = os.environ.get("GID_JWT_SECRET_KEY")
    old_root = os.environ.get("GID_DOWNLOAD__OUTPUT_DIRECTORY")

    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    os.environ["GID_API_DATABASE_URL"] = db_url
    os.environ["GID_JWT_SECRET_KEY"] = "step03e2-super-secret-jwt-key-32bytes!!"
    os.environ["GID_DOWNLOAD__OUTPUT_DIRECTORY"] = str(storage_root)
    os.environ["GID_API_SKIP_SEED"] = "1"

    from api.config import get_api_settings
    get_api_settings.cache_clear()

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _init_schema():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as s:
            # Tenant A
            t_a = TenantDB(id="tenant-a", name="Tenant A", slug="tenant-a", tier="pro", is_active=True)
            alice = UserDB(
                id="alice-id",
                tenant_id="tenant-a",
                email="alice@tenant-a.com",
                display_name="Alice Owner",
                role="owner",
                is_active=True,
                password_hash="fakehash",
            )
            bob = UserDB(
                id="bob-id",
                tenant_id="tenant-a",
                email="bob@tenant-a.com",
                display_name="Bob Member",
                role="member",
                is_active=True,
                password_hash="fakehash",
            )

            # Tenant B
            t_b = TenantDB(id="tenant-b", name="Tenant B", slug="tenant-b", tier="basic", is_active=True)
            charlie = UserDB(
                id="charlie-id",
                tenant_id="tenant-b",
                email="charlie@tenant-b.com",
                display_name="Charlie Owner",
                role="owner",
                is_active=True,
                password_hash="fakehash",
            )

            s.add_all([t_a, alice, bob, t_b, charlie])
            await s.commit()

            # Create physical media files in Tenant A and Tenant B
            t_a_downloads = storage_root / "tenant-a" / "downloads"
            t_a_downloads.mkdir(parents=True, exist_ok=True)
            alice_vid = t_a_downloads / "alice_private.mp4"
            alice_vid.write_bytes(b"ALICE_MEDIA_DATA_1234567890")

            bob_vid = t_a_downloads / "bob_private.mp4"
            bob_vid.write_bytes(b"BOB_MEDIA_DATA_1234567890")

            t_a_clips = storage_root / "tenant-a" / "clips"
            t_a_clips.mkdir(parents=True, exist_ok=True)
            alice_clip = t_a_clips / "clip_alice123.mp4"
            alice_clip.write_bytes(b"ALICE_CLIP_DATA_1234567890")

            t_b_downloads = storage_root / "tenant-b" / "downloads"
            t_b_downloads.mkdir(parents=True, exist_ok=True)
            charlie_vid = t_b_downloads / "charlie_private.mp4"
            charlie_vid.write_bytes(b"CHARLIE_MEDIA_DATA_1234567890")

            # Add DownloadJobDB for Alice's video
            job_alice = DownloadJobDB(
                id="dl-alice-001",
                tenant_id="tenant-a",
                user_id="alice-id",
                url="https://youtube.com/watch?v=alice",
                provider="youtube",
                title="alice_private",
                file_path=str(alice_vid),
                status="completed",
                media_type="video",
            )
            # Add DownloadJobDB for Bob's video
            job_bob = DownloadJobDB(
                id="dl-bob-001",
                tenant_id="tenant-a",
                user_id="bob-id",
                url="https://youtube.com/watch?v=bob",
                provider="youtube",
                title="bob_private",
                file_path=str(bob_vid),
                status="completed",
                media_type="video",
            )
            # Add DownloadJobDB for Charlie's video
            job_charlie = DownloadJobDB(
                id="dl-charlie-001",
                tenant_id="tenant-b",
                user_id="charlie-id",
                url="https://youtube.com/watch?v=charlie",
                provider="youtube",
                title="charlie_private",
                file_path=str(charlie_vid),
                status="completed",
                media_type="video",
            )

            # Add MediaArtifactDB for Alice's clip
            art_alice = MediaArtifactDB(
                id="art-alice-001",
                tenant_id="tenant-a",
                user_id="alice-id",
                artifact_type="clip",
                storage_path="clips/clip_alice123.mp4",
                filename="clip_alice123.mp4",
                format="mp4",
                status="ready",
                file_size_bytes=len(b"ALICE_CLIP_DATA_1234567890"),
            )

            s.add_all([job_alice, job_bob, job_charlie, art_alice])
            await s.commit()

    asyncio.run(_init_schema())

    yield {
        "engine": engine,
        "session_factory": session_factory,
        "storage_root": storage_root,
    }

    asyncio.run(engine.dispose())
    if old_db:
        os.environ["GID_API_DATABASE_URL"] = old_db
    else:
        os.environ.pop("GID_API_DATABASE_URL", None)
    if old_jwt:
        os.environ["GID_JWT_SECRET_KEY"] = old_jwt
    if old_root:
        os.environ["GID_DOWNLOAD__OUTPUT_DIRECTORY"] = old_root
    else:
        os.environ.pop("GID_DOWNLOAD__OUTPUT_DIRECTORY", None)
    get_api_settings.cache_clear()


@pytest.mark.asyncio
async def test_01_valid_download_id_access(isolation_env):
    """1. Test that owner/creator can access their own valid download ID."""
    async with isolation_env["session_factory"]() as db:
        alice = CurrentUser(
            user_id="alice-id",
            tenant_id="tenant-a",
            role="owner",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        resolved = await resolve_source_media_reference(db, "dl-alice-001", "tenant-a", user=alice)
        assert "alice_private.mp4" in resolved
        await verify_source_media_authorization(db, resolved, alice)


@pytest.mark.asyncio
async def test_02_valid_artifact_id_access(isolation_env):
    """2. Test that owner/creator can access their own valid artifact ID."""
    async with isolation_env["session_factory"]() as db:
        alice = CurrentUser(
            user_id="alice-id",
            tenant_id="tenant-a",
            role="owner",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        resolved = await resolve_source_media_reference(db, "art-alice-001", "tenant-a", user=alice)
        assert resolved == "clips/clip_alice123.mp4"
        await verify_source_media_authorization(db, resolved, alice)


@pytest.mark.asyncio
async def test_03_another_member_download_id_denied(isolation_env):
    """3. Test that Bob (member) CANNOT access Alice's download ID even though both are in Tenant A."""
    async with isolation_env["session_factory"]() as db:
        bob = CurrentUser(
            user_id="bob-id",
            tenant_id="tenant-a",
            role="member",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        with pytest.raises(HTTPException) as exc_info:
            await resolve_source_media_reference(db, "dl-alice-001", "tenant-a", user=bob)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_04_another_member_artifact_id_denied(isolation_env):
    """4. Test that Bob (member) CANNOT access Alice's artifact ID even though both are in Tenant A."""
    async with isolation_env["session_factory"]() as db:
        bob = CurrentUser(
            user_id="bob-id",
            tenant_id="tenant-a",
            role="member",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        with pytest.raises(HTTPException) as exc_info:
            await resolve_source_media_reference(db, "art-alice-001", "tenant-a", user=bob)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_05_another_member_relative_path_denied(isolation_env):
    """5. Test that Bob (member) CANNOT bypass authorization by supplying Alice's relative file path."""
    async with isolation_env["session_factory"]() as db:
        bob = CurrentUser(
            user_id="bob-id",
            tenant_id="tenant-a",
            role="member",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        # Attempting to resolve Alice's video directly by relative path
        with pytest.raises(HTTPException) as exc_info:
            await resolve_source_media_reference(db, "downloads/alice_private.mp4", "tenant-a", user=bob)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_06_absolute_container_path_rejected(isolation_env):
    """6. Test that supplying absolute filesystem paths is rejected with HTTP 400 for public callers."""
    async with isolation_env["session_factory"]() as db:
        bob = CurrentUser(
            user_id="bob-id",
            tenant_id="tenant-a",
            role="member",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        with pytest.raises(HTTPException) as exc_info:
            await resolve_source_media_reference(
                db,
                "/app/downloads/tenant-a/downloads/alice_private.mp4",
                "tenant-a",
                user=bob,
            )
        assert exc_info.value.status_code == 400
        assert "Absolute filesystem paths are prohibited" in exc_info.value.detail


@pytest.mark.asyncio
async def test_07_cross_tenant_media_id_denied(isolation_env):
    """7. Test that Charlie (Tenant B) CANNOT access Tenant A's download or artifact IDs."""
    async with isolation_env["session_factory"]() as db:
        charlie = CurrentUser(
            user_id="charlie-id",
            tenant_id="tenant-b",
            role="owner",
            tier="basic",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        # Attempting to resolve Alice's download from Tenant B
        with pytest.raises(HTTPException) as exc_info:
            resolved = await resolve_source_media_reference(db, "job:dl-alice-001", "tenant-b", user=charlie)
            resolve_and_authorize_clip_source(resolved, "tenant-b")
        assert exc_info.value.status_code == 404

        # Attempting to resolve Alice's artifact from Tenant B
        with pytest.raises(HTTPException) as exc_info2:
            resolved2 = await resolve_source_media_reference(db, "art:art-alice-001", "tenant-b", user=charlie)
            resolve_and_authorize_clip_source(resolved2, "tenant-b")
        assert exc_info2.value.status_code == 404


@pytest.mark.asyncio
async def test_08_cross_tenant_relative_or_absolute_path_denied(isolation_env):
    """8. Test that cross-tenant path references are strictly rejected."""
    async with isolation_env["session_factory"]() as db:
        charlie = CurrentUser(
            user_id="charlie-id",
            tenant_id="tenant-b",
            role="owner",
            tier="basic",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        # Absolute path to Tenant A
        with pytest.raises(HTTPException) as exc_info:
            await resolve_source_media_reference(
                db,
                "/app/downloads/tenant-a/downloads/alice_private.mp4",
                "tenant-b",
                user=charlie,
            )
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_09_symlink_escape_denied(isolation_env):
    """9. Test that symlinks pointing outside tenant boundary are rejected."""
    root = isolation_env["storage_root"]
    tenant_a_dir = root / "tenant-a" / "downloads"
    secret_dir = root / "system_secrets"
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_file = secret_dir / "secret.txt"
    secret_file.write_text("SUPER_SECRET_PAYLOAD")

    symlink_file = tenant_a_dir / "evil_symlink.mp4"
    try:
        symlink_file.symlink_to(secret_file)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation requires elevated permissions on this platform")

    with pytest.raises(Exception):
        resolve_and_authorize_clip_source("downloads/evil_symlink.mp4", "tenant-a")


@pytest.mark.asyncio
async def test_10_path_traversal_rejected(isolation_env):
    """10. Test that directory traversal sequences ('../') are rejected with HTTP 400."""
    async with isolation_env["session_factory"]() as db:
        bob = CurrentUser(
            user_id="bob-id",
            tenant_id="tenant-a",
            role="member",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        with pytest.raises(HTTPException) as exc_info:
            await resolve_source_media_reference(
                db,
                "../../tenant-b/downloads/charlie_private.mp4",
                "tenant-a",
                user=bob,
            )
        assert exc_info.value.status_code == 400
        assert "traversal" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_11_authorized_owner_administrative_oversight(isolation_env):
    """11. Test that Alice (owner of Tenant A) CAN oversee and access Bob's (member) media."""
    async with isolation_env["session_factory"]() as db:
        alice = CurrentUser(
            user_id="alice-id",
            tenant_id="tenant-a",
            role="owner",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        resolved = await resolve_source_media_reference(db, "dl-bob-001", "tenant-a", user=alice)
        assert "bob_private.mp4" in resolved
        # Owner authorization should succeed without exception
        await verify_source_media_authorization(db, resolved, alice)


@pytest.mark.asyncio
async def test_12_api_key_scoping_and_ownership(isolation_env):
    """12. Test that API keys cannot exceed permissions and can only access their authorized media."""
    async with isolation_env["session_factory"]() as db:
        # Key with clips:write but NOT matching owner
        key_user = CurrentUser(
            user_id="service:key-123",
            tenant_id="tenant-a",
            role="service_account",
            tier="pro",
            auth_method=AuthMethod.API_KEY,
            api_key_id="key-123",
            permissions=["clips:write"],
        )
        # Attempting to access Alice's private video via API key
        with pytest.raises(HTTPException) as exc_info:
            await resolve_source_media_reference(db, "dl-alice-001", "tenant-a", user=key_user)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_13_worker_replay_prevention_on_ownership_change(isolation_env):
    """13. Test that worker revalidation fails closed if source ownership changed after job queued."""
    async with isolation_env["session_factory"]() as db:
        # Simulate a queued job created by Bob referencing his video
        req = ClipExtractRequest(
            source="dl-bob-001",
            start_time="00:00:01",
            end_time="00:00:05",
            mode="fast",
        )
        bob = CurrentUser(
            user_id="bob-id",
            tenant_id="tenant-a",
            role="member",
            tier="pro",
            auth_method=AuthMethod.JWT,
            permissions=["clips:write", "clips:read"],
        )
        job = await submit_clip_job(db, bob, req)
        assert job.id is not None
        assert job.requester_user_id == "bob-id"

        # Now simulate an out-of-band ownership change: Bob's download job is transferred to Alice or deleted
        dl_bob = (await db.execute(select(DownloadJobDB).where(DownloadJobDB.id == "dl-bob-001"))).scalar_one()
        dl_bob.user_id = "alice-id"
        await db.commit()

        # Worker dequeues job and attempts revalidation with synthetic user for Bob
        synthetic_user = CurrentUser(
            user_id=job.requester_user_id,
            tenant_id=job.tenant_id,
            role="member",
            auth_method=AuthMethod.JWT,
        )

        with pytest.raises(HTTPException) as exc_info:
            resolved = await resolve_source_media_reference(db, job.request_payload["source"], job.tenant_id, user=synthetic_user)
            await verify_source_media_authorization(db, resolved, synthetic_user)
        assert exc_info.value.status_code == 404
