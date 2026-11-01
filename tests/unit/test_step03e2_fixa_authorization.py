"""Targeted unit tests for MediaForge STEP 03-E2-Fix-A.

Validates:
1. Two users with identical media basenames in same tenant.
2. SQL wildcard characters (% and _) in filenames without pattern leakage.
3. Same user with duplicate filenames in different directories (ambiguity fail-closed).
4. Cross-tenant ID and path references (404 fail-closed).
5. Authorized tenant owner oversight.
6. Member-to-member access denial (404).
7. API key with insufficient scope.
8. Revoked API key before worker execution (MediaSecurityError).
9. Deactivated requester before worker execution (MediaSecurityError).
10. Deleted source before worker execution (MediaSecurityError).
11. Symlink escape rejection (MediaSecurityError / 400).
12. Unauthorized relative traversal and absolute paths (400).
13. Valid download-to-clip flow using server-issued unique ID.
14. Valid artifact-to-merge flow using server-issued unique ID.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.models import APIKeyDB, Base, DownloadJobDB, MediaArtifactDB, MediaWorkItemDB, UserDB
from api.dependencies import AuthMethod, CurrentUser
from api.security.media_storage import (
    MediaSecurityError,
    resolve_and_authorize_clip_source,
    resolve_tenant_artifact_path,
)
from api.services.artifact_service import (
    escape_sql_like,
    resolve_source_media_reference,
    verify_source_media_authorization,
)
from api.workers.media_worker import revalidate_worker_requester


@pytest.fixture
def env_fixture(tmp_path):
    """Set up isolated multi-tenant environment with SQLite DB and storage."""
    db_file = tmp_path / "fixa_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    old_db = os.environ.get("GID_API_DATABASE_URL")
    old_root = os.environ.get("GID_DOWNLOAD__OUTPUT_DIRECTORY")

    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    t_a_root = storage_root / "tenant_alpha"
    t_b_root = storage_root / "tenant_beta"
    for sub in ("downloads", "clips", "merged"):
        (t_a_root / sub).mkdir(parents=True, exist_ok=True)
        (t_b_root / sub).mkdir(parents=True, exist_ok=True)

    os.environ["GID_API_DATABASE_URL"] = db_url
    os.environ["GID_DOWNLOAD__OUTPUT_DIRECTORY"] = str(storage_root)

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _init_schema():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init_schema())

    yield {
        "session_factory": session_factory,
        "engine": engine,
        "root": storage_root,
        "tenant_a": t_a_root,
        "tenant_b": t_b_root,
    }

    async def _close():
        await engine.dispose()

    asyncio.run(_close())

    if old_db:
        os.environ["GID_API_DATABASE_URL"] = old_db
    else:
        os.environ.pop("GID_API_DATABASE_URL", None)

    if old_root:
        os.environ["GID_DOWNLOAD__OUTPUT_DIRECTORY"] = old_root
    else:
        os.environ.pop("GID_DOWNLOAD__OUTPUT_DIRECTORY", None)


def test_01_two_users_identical_basenames(env_fixture):
    """Two users have files with the same basename in the same tenant.

    User A referencing the filename gets their own file.
    User B referencing the filename gets their own file.
    Owner referencing the filename gets HTTP 409 Conflict due to ambiguity.
    Owner referencing exact job ID resolves without conflict.
    """
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a"]

    (t_a / "downloads" / "alice_video.mp4").write_bytes(b"ALICE")
    (t_a / "downloads" / "bob_video.mp4").write_bytes(b"BOB")

    async def _run():
        async with session_factory() as db:
            job_alice = DownloadJobDB(
                id="job-alice-vacation",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=alice",
                provider="youtube",
                title="vacation",
                file_path="downloads/alice_video.mp4",
                status="completed",
            )
            job_bob = DownloadJobDB(
                id="job-bob-vacation",
                tenant_id="tenant_alpha",
                user_id="user_bob",
                url="https://youtube.com/watch?v=bob",
                provider="youtube",
                title="vacation",
                file_path="downloads/bob_video.mp4",
                status="completed",
            )
            db.add_all([job_alice, job_bob])
            await db.commit()

            user_alice = CurrentUser(user_id="user_alice", tenant_id="tenant_alpha", role="member")
            user_bob = CurrentUser(user_id="user_bob", tenant_id="tenant_alpha", role="member")
            user_owner = CurrentUser(user_id="user_owner", tenant_id="tenant_alpha", role="owner")

            # 1. Alice resolves 'vacation' -> matches ONLY Alice's record
            resolved_alice = await resolve_source_media_reference(db, "vacation", "tenant_alpha", user_alice)
            assert resolved_alice == "downloads/alice_video.mp4"

            # 2. Bob resolves 'vacation' -> matches ONLY Bob's record
            resolved_bob = await resolve_source_media_reference(db, "vacation", "tenant_alpha", user_bob)
            assert resolved_bob == "downloads/bob_video.mp4"

            # 3. Owner resolves 'vacation' -> ambiguous match across tenant -> 409 Conflict
            with pytest.raises(HTTPException) as exc_info:
                await resolve_source_media_reference(db, "vacation", "tenant_alpha", user_owner)
            assert exc_info.value.status_code == 409
            assert "Ambiguous media reference" in exc_info.value.detail

            # 4. Owner resolves exact server-issued ID -> success
            resolved_owner_id = await resolve_source_media_reference(db, "job:job-alice-vacation", "tenant_alpha", user_owner)
            assert resolved_owner_id == "downloads/alice_video.mp4"

    asyncio.run(_run())


def test_02_sql_wildcard_character_protection(env_fixture):
    """Filenames with '%' and '_' must not act as wildcards in database queries."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a"]

    (t_a / "downloads" / "test%video.mp4").write_bytes(b"PERCENT")
    (t_a / "downloads" / "test_video.mp4").write_bytes(b"UNDERSCORE")
    (t_a / "downloads" / "test123video.mp4").write_bytes(b"OTHER")

    async def _run():
        async with session_factory() as db:
            job_percent = DownloadJobDB(
                id="job-percent",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=pct",
                provider="youtube",
                title="test%video",
                file_path="downloads/test%video.mp4",
                status="completed",
            )
            job_under = DownloadJobDB(
                id="job-under",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=und",
                provider="youtube",
                title="test_video",
                file_path="downloads/test_video.mp4",
                status="completed",
            )
            job_other = DownloadJobDB(
                id="job-other",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=oth",
                provider="youtube",
                title="test123video",
                file_path="downloads/test123video.mp4",
                status="completed",
            )
            db.add_all([job_percent, job_under, job_other])
            await db.commit()

            user_alice = CurrentUser(user_id="user_alice", tenant_id="tenant_alpha", role="member")

            resolved = await resolve_source_media_reference(db, "downloads/test%video.mp4", "tenant_alpha", user_alice)
            assert resolved == "downloads/test%video.mp4"

            resolved_under = await resolve_source_media_reference(db, "downloads/test_video.mp4", "tenant_alpha", user_alice)
            assert resolved_under == "downloads/test_video.mp4"

    asyncio.run(_run())


def test_03_duplicate_filenames_different_directories(env_fixture):
    """Same user has duplicate filenames in different directories. Generic reference fails closed with 409."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a"]

    (t_a / "downloads" / "clip.mp4").write_bytes(b"DOWNLOAD_CLIP")
    (t_a / "clips" / "clip.mp4").write_bytes(b"RENDERED_CLIP")

    async def _run():
        async with session_factory() as db:
            job1 = DownloadJobDB(
                id="dl-dup-01",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=dup1",
                provider="youtube",
                title="clip",
                file_path="downloads/clip.mp4",
                status="completed",
            )
            art1 = MediaArtifactDB(
                id="art-dup-01",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                artifact_type="clip",
                filename="clip.mp4",
                storage_path="clips/clip.mp4",
                status="completed",
            )
            db.add_all([job1, art1])
            await db.commit()

            user_alice = CurrentUser(user_id="user_alice", tenant_id="tenant_alpha", role="member")

            with pytest.raises(HTTPException) as exc_info:
                await resolve_source_media_reference(db, "clip.mp4", "tenant_alpha", user_alice)
            assert exc_info.value.status_code == 409

            assert await resolve_source_media_reference(db, "job:dl-dup-01", "tenant_alpha", user_alice) == "downloads/clip.mp4"
            assert await resolve_source_media_reference(db, "art:art-dup-01", "tenant_alpha", user_alice) == "clips/clip.mp4"

    asyncio.run(_run())


def test_04_cross_tenant_id_and_path_references(env_fixture):
    """Cross-tenant media references fail closed with 404."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a"]

    (t_a / "downloads" / "secret_a.mp4").write_bytes(b"SECRET_A")

    async def _run():
        async with session_factory() as db:
            job_a = DownloadJobDB(
                id="job-secret-a",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=sec",
                provider="youtube",
                title="secret",
                file_path="downloads/secret_a.mp4",
                status="completed",
            )
            db.add(job_a)
            await db.commit()

            user_charlie = CurrentUser(user_id="user_charlie", tenant_id="tenant_beta", role="owner")

            with pytest.raises(HTTPException) as exc_info:
                await resolve_source_media_reference(db, "job:job-secret-a", "tenant_beta", user_charlie)
            assert exc_info.value.status_code == 404

            with pytest.raises(HTTPException) as exc_info:
                resolve_and_authorize_clip_source("downloads/secret_a.mp4", "tenant_beta", "user_charlie")
            assert exc_info.value.status_code == 404

    asyncio.run(_run())


def test_05_authorized_tenant_owner_oversight(env_fixture):
    """Tenant Owner can access files owned by members within their tenant."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a"]

    (t_a / "downloads" / "alice_unique.mp4").write_bytes(b"UNIQUE_ALICE")

    async def _run():
        async with session_factory() as db:
            job_alice = DownloadJobDB(
                id="job-alice-unique",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=uniq",
                provider="youtube",
                title="alice_unique",
                file_path="downloads/alice_unique.mp4",
                status="completed",
            )
            db.add(job_alice)
            await db.commit()

            user_owner = CurrentUser(user_id="user_owner", tenant_id="tenant_alpha", role="owner")

            resolved = await resolve_source_media_reference(db, "job:job-alice-unique", "tenant_alpha", user_owner)
            assert resolved == "downloads/alice_unique.mp4"

    asyncio.run(_run())


def test_06_member_to_member_access_denial(env_fixture):
    """Member B cannot access Member A's file in the same tenant (fails with 404)."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a"]

    (t_a / "downloads" / "alice_private.mp4").write_bytes(b"PRIVATE")

    async def _run():
        async with session_factory() as db:
            job_alice = DownloadJobDB(
                id="job-alice-priv",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=priv",
                provider="youtube",
                title="alice_private",
                file_path="downloads/alice_private.mp4",
                status="completed",
            )
            db.add(job_alice)
            await db.commit()

            user_bob = CurrentUser(user_id="user_bob", tenant_id="tenant_alpha", role="member")

            with pytest.raises(HTTPException) as exc_info:
                await resolve_source_media_reference(db, "job:job-alice-priv", "tenant_alpha", user_bob)
            assert exc_info.value.status_code == 404

    asyncio.run(_run())


def test_07_api_key_insufficient_scope(env_fixture):
    """API key lacking clips/media processing scope fails worker reauthorization."""
    session_factory = env_fixture["session_factory"]

    async def _run():
        async with session_factory() as db:
            ak = APIKeyDB(
                id="ak-readonly",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                name="readonly",
                key_prefix="mf_test",
                key_hash="hash123",
                permissions=["downloads:read", "transcripts:read"],  # No clips:write
                is_active=True,
                is_revoked=False,
            )
            db.add(ak)
            await db.commit()

            job = MediaWorkItemDB(
                id="job-scoped-01",
                tenant_id="tenant_alpha",
                operation_type="clip",
                requester_api_key_id="ak-readonly",
                status="queued",
            )

            with pytest.raises(MediaSecurityError) as exc_info:
                await revalidate_worker_requester(db, job)
            assert "lacks required clip/media scopes" in str(exc_info.value)

    asyncio.run(_run())


def test_08_revoked_api_key_before_worker_execution(env_fixture):
    """API key revoked before worker processes work item fails closed."""
    session_factory = env_fixture["session_factory"]

    async def _run():
        async with session_factory() as db:
            ak = APIKeyDB(
                id="ak-revoked-01",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                name="revoked_key",
                key_prefix="mf_test",
                key_hash="hash456",
                permissions=["clips:write"],
                is_active=True,
                is_revoked=True,
                revoked_at=datetime.now(timezone.utc),
            )
            db.add(ak)
            await db.commit()

            job = MediaWorkItemDB(
                id="job-revoked-test",
                tenant_id="tenant_alpha",
                operation_type="clip",
                requester_api_key_id="ak-revoked-01",
                status="queued",
            )

            with pytest.raises(MediaSecurityError) as exc_info:
                await revalidate_worker_requester(db, job)
            assert "has been revoked" in str(exc_info.value)

    asyncio.run(_run())


def test_09_deactivated_requester_before_worker_execution(env_fixture):
    """User deactivated before worker execution causes work item failure."""
    session_factory = env_fixture["session_factory"]

    async def _run():
        async with session_factory() as db:
            user_inactive = UserDB(
                id="user-inactive-01",
                tenant_id="tenant_alpha",
                email="inactive@example.com",
                display_name="Inactive User",
                password_hash="pwd",
                auth_provider="local",
                role="member",
                is_active=False,
            )
            db.add(user_inactive)
            await db.commit()

            job = MediaWorkItemDB(
                id="job-inactive-test",
                tenant_id="tenant_alpha",
                operation_type="clip",
                requester_user_id="user-inactive-01",
                status="queued",
            )

            with pytest.raises(MediaSecurityError) as exc_info:
                await revalidate_worker_requester(db, job)
            assert "is deactivated or suspended" in str(exc_info.value)

    asyncio.run(_run())


def test_10_deleted_source_before_worker_execution(env_fixture):
    """Physical source file deleted before worker runs causes worker to reject execution."""
    session_factory = env_fixture["session_factory"]

    missing_file = "downloads/deleted_file.mp4"

    async def _run():
        async with session_factory() as db:
            job_entry = DownloadJobDB(
                id="dl-deleted-source",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=del",
                provider="youtube",
                title="deleted_file",
                file_path=missing_file,
                status="completed",
            )
            user_active = UserDB(
                id="user_alice",
                tenant_id="tenant_alpha",
                email="alice@example.com",
                display_name="Alice",
                password_hash="pwd",
                auth_provider="local",
                role="member",
                is_active=True,
            )
            db.add_all([job_entry, user_active])
            await db.commit()

            user_alice = CurrentUser(user_id="user_alice", tenant_id="tenant_alpha", role="member")
            resolved = await resolve_source_media_reference(db, "job:dl-deleted-source", "tenant_alpha", user_alice)
            assert resolved == missing_file

            with pytest.raises(HTTPException) as exc_info:
                resolve_and_authorize_clip_source(
                    source=resolved,
                    tenant_id="tenant_alpha",
                    user_id="user_alice",
                    is_admin=False,
                )
            assert exc_info.value.status_code == 404

    asyncio.run(_run())


def test_11_symlink_escape_rejection(env_fixture):
    """Symlink pointing outside the tenant storage root is rejected."""
    t_a = env_fixture["tenant_a"]
    external_secret = env_fixture["root"] / "outside_secret.txt"
    external_secret.write_bytes(b"HOST_ROOT_SECRET")

    link_path = t_a / "downloads" / "symlink_escape.mp4"
    try:
        os.symlink(external_secret, link_path)
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on this Windows platform")

    with pytest.raises(MediaSecurityError) as exc_info:
        resolve_tenant_artifact_path("downloads/symlink_escape.mp4", "tenant_alpha")
    assert "tenant boundary" in str(exc_info.value)


def test_12_unauthorized_relative_and_absolute_paths(env_fixture):
    """Traversal tokens ('..') and absolute paths are rejected with HTTP 400."""
    session_factory = env_fixture["session_factory"]
    user_alice = CurrentUser(user_id="user_alice", tenant_id="tenant_alpha", role="member")
    user_admin = CurrentUser(user_id="user_admin", tenant_id="tenant_alpha", role="admin")

    async def _run():
        async with session_factory() as db:
            # Path traversal rejected for member
            with pytest.raises(HTTPException) as exc_info:
                await resolve_source_media_reference(db, "../../etc/passwd", "tenant_alpha", user_alice)
            assert exc_info.value.status_code == 400
            assert "Path traversal token ('..') is prohibited" in exc_info.value.detail

            # Absolute path rejected for member
            with pytest.raises(HTTPException) as exc_info:
                await resolve_source_media_reference(db, "/app/downloads/tenant_alpha/sample.mp4", "tenant_alpha", user_alice)
            assert exc_info.value.status_code == 400
            assert "Absolute filesystem paths are prohibited" in exc_info.value.detail

            # Absolute path rejected even for tenant admin in public API call
            with pytest.raises(HTTPException) as exc_info:
                await resolve_source_media_reference(db, "C:\\Windows\\System32\\cmd.exe", "tenant_alpha", user_admin)
            assert exc_info.value.status_code == 400

    asyncio.run(_run())


def test_13_valid_download_to_clip_flow_server_id(env_fixture):
    """Valid download-to-clip flow using server-issued ID resolves cleanly."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a"]

    (t_a / "downloads" / "me_at_the_zoo.mp4").write_bytes(b"ZOO_MEDIA_CONTENT")

    async def _run():
        async with session_factory() as db:
            job = DownloadJobDB(
                id="ad5222b7-eb91-49cd-a0a2-4824ff23013a",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                url="https://youtube.com/watch?v=zoo",
                provider="youtube",
                title="Me at the zoo",
                file_path="downloads/me_at_the_zoo.mp4",
                status="completed",
            )
            db.add(job)
            await db.commit()

            user_alice = CurrentUser(user_id="user_alice", tenant_id="tenant_alpha", role="member")

            resolved = await resolve_source_media_reference(
                db,
                "ad5222b7-eb91-49cd-a0a2-4824ff23013a",
                "tenant_alpha",
                user_alice,
            )
            assert resolved == "downloads/me_at_the_zoo.mp4"

            resolved_prefixed = await resolve_source_media_reference(
                db,
                "job:ad5222b7-eb91-49cd-a0a2-4824ff23013a",
                "tenant_alpha",
                user_alice,
            )
            assert resolved_prefixed == "downloads/me_at_the_zoo.mp4"

            abs_path = resolve_and_authorize_clip_source(
                source=resolved,
                tenant_id="tenant_alpha",
                user_id="user_alice",
                is_admin=False,
            )
            assert abs_path.is_file()
            assert abs_path.name == "me_at_the_zoo.mp4"

    asyncio.run(_run())


def test_14_valid_artifact_to_merge_flow_server_id(env_fixture):
    """Valid artifact-to-merge flow using server-issued ID resolves cleanly."""
    session_factory = env_fixture["session_factory"]
    t_a = env_fixture["tenant_a"]

    (t_a / "clips" / "clip_alpha.mp4").write_bytes(b"CLIP_A")
    (t_a / "clips" / "clip_beta.mp4").write_bytes(b"CLIP_B")

    async def _run():
        async with session_factory() as db:
            art_a = MediaArtifactDB(
                id="art-713e8e446000",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                artifact_type="clip",
                filename="clip_alpha.mp4",
                storage_path="clips/clip_alpha.mp4",
                status="completed",
            )
            art_b = MediaArtifactDB(
                id="art-824f9f557111",
                tenant_id="tenant_alpha",
                user_id="user_alice",
                artifact_type="clip",
                filename="clip_beta.mp4",
                storage_path="clips/clip_beta.mp4",
                status="completed",
            )
            db.add_all([art_a, art_b])
            await db.commit()

            user_alice = CurrentUser(user_id="user_alice", tenant_id="tenant_alpha", role="member")

            resolved_a = await resolve_source_media_reference(db, "art:art-713e8e446000", "tenant_alpha", user_alice)
            resolved_b = await resolve_source_media_reference(db, "art:art-824f9f557111", "tenant_alpha", user_alice)

            assert resolved_a == "clips/clip_alpha.mp4"
            assert resolved_b == "clips/clip_beta.mp4"

            abs_a = resolve_and_authorize_clip_source(source=resolved_a, tenant_id="tenant_alpha", user_id="user_alice")
            abs_b = resolve_and_authorize_clip_source(source=resolved_b, tenant_id="tenant_alpha", user_id="user_alice")

            assert abs_a.is_file()
            assert abs_b.is_file()

    asyncio.run(_run())
