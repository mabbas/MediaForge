"""Unit and integration test suite for STEP 03-E2-Fix-B.

Verifies execution-time media ownership revalidation, worker authorization,
and typed error classification:
1. Ownership transfer after job enqueue (Member A -> Member B).
2. Artifact ownership change before merge execution.
3. Deactivated user before worker execution.
4. Revoked API key before worker execution.
5. Deleted DB source record before execution.
6. Cross-tenant media reference rejected at worker runtime.
7. Legitimate tenant admin oversight authorized.
8. Typed error classification and error sanitization.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.models import (
    APIKeyDB,
    Base,
    DownloadJobDB,
    MediaArtifactDB,
    MediaWorkItemDB,
    TenantDB,
    UserDB,
)
from api.dependencies import AuthMethod, CurrentUser
from api.security.media_storage import MediaSecurityError, get_tenant_storage_root
from api.services.job_service import submit_clip_job, submit_merge_job, fail_job
from api.workers.media_worker import (
    MediaWorker,
    is_retryable_error,
    revalidate_worker_requester,
)


@pytest.fixture
def env_fixture(tmp_path):
    """Setup isolated async SQLite and storage directories."""
    db_file = tmp_path / "fixb_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    tenant_a_id = f"tenant_a_{uuid.uuid4().hex[:6]}"
    tenant_b_id = f"tenant_b_{uuid.uuid4().hex[:6]}"

    t_a_root = storage_root / tenant_a_id
    t_b_root = storage_root / tenant_b_id
    for sub in ("downloads", "clips", "merged"):
        (t_a_root / sub).mkdir(parents=True, exist_ok=True)
        (t_b_root / sub).mkdir(parents=True, exist_ok=True)

    bob_file = t_a_root / "downloads" / "bob_video.mp4"
    bob_file.write_bytes(b"dummy mp4 video stream content for bob")

    old_db = os.environ.get("GID_API_DATABASE_URL")
    old_root = os.environ.get("GID_DOWNLOAD__OUTPUT_DIRECTORY")
    old_storage = os.environ.get("MEDIA_STORAGE_DIR")

    os.environ["GID_API_DATABASE_URL"] = db_url
    os.environ["GID_DOWNLOAD__OUTPUT_DIRECTORY"] = str(storage_root)
    os.environ["MEDIA_STORAGE_DIR"] = str(storage_root)

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    alice_id = f"alice_{uuid.uuid4().hex[:6]}"
    bob_id = f"bob_{uuid.uuid4().hex[:6]}"
    charlie_id = f"charlie_{uuid.uuid4().hex[:6]}"
    david_id = f"david_{uuid.uuid4().hex[:6]}"

    async def _init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as s:
            s.add_all([
                TenantDB(id=tenant_a_id, name="Tenant A", slug=tenant_a_id, tier="pro"),
                TenantDB(id=tenant_b_id, name="Tenant B", slug=tenant_b_id, tier="pro"),
                UserDB(id=alice_id, tenant_id=tenant_a_id, email="alice@a.com", role="owner", is_active=True),
                UserDB(id=bob_id, tenant_id=tenant_a_id, email="bob@a.com", role="member", is_active=True),
                UserDB(id=charlie_id, tenant_id=tenant_a_id, email="charlie@a.com", role="member", is_active=True),
                UserDB(id=david_id, tenant_id=tenant_b_id, email="david@b.com", role="owner", is_active=True),
            ])
            await s.commit()

    asyncio.run(_init_db())

    yield {
        "session_factory": session_factory,
        "engine": engine,
        "tenant_a": tenant_a_id,
        "tenant_b": tenant_b_id,
        "alice": alice_id,
        "bob": bob_id,
        "charlie": charlie_id,
        "david": david_id,
        "t_a_root": t_a_root,
        "bob_file": bob_file,
    }

    async def _cleanup():
        await engine.dispose()

    asyncio.run(_cleanup())

    if old_db:
        os.environ["GID_API_DATABASE_URL"] = old_db
    else:
        os.environ.pop("GID_API_DATABASE_URL", None)

    if old_root:
        os.environ["GID_DOWNLOAD__OUTPUT_DIRECTORY"] = old_root
    else:
        os.environ.pop("GID_DOWNLOAD__OUTPUT_DIRECTORY", None)

    if old_storage:
        os.environ["MEDIA_STORAGE_DIR"] = old_storage
    else:
        os.environ.pop("MEDIA_STORAGE_DIR", None)


def test_01_ownership_transferred_after_submission_fails_worker(env_fixture):
    """Member B submits a clip job. Before worker claims it, the underlying media is

    transferred to Charlie. Worker must refuse execution and fail the job without FFmpeg.
    """
    session_factory = env_fixture["session_factory"]
    tenant_a = env_fixture["tenant_a"]
    bob_id = env_fixture["bob"]
    charlie_id = env_fixture["charlie"]
    dl_id = f"dl_{uuid.uuid4().hex[:8]}"

    async def _test():
        async with session_factory() as s:
            dl = DownloadJobDB(
                id=dl_id,
                tenant_id=tenant_a,
                user_id=bob_id,
                url="https://youtube.com/watch?v=123",
                provider="youtube",
                title="bob_video",
                status="completed",
                file_path="downloads/bob_video.mp4",
            )
            s.add(dl)
            await s.commit()

            bob_user = CurrentUser(
                user_id=bob_id,
                tenant_id=tenant_a,
                role="member",
                auth_method=AuthMethod.JWT,
            )

            from api.schemas.clips import ClipExtractRequest
            req = ClipExtractRequest(
                source=f"job:{dl_id}",
                start_time="00:00:01",
                end_time="00:00:03",
                output_format="mp4",
            )
            job = await submit_clip_job(s, bob_user, req)
            assert job.status == "queued"

            # Malicious / Administrative Transfer: Ownership reassigned to Charlie!
            dl.user_id = charlie_id
            await s.commit()

            # Worker claims and attempts execution with Bob's requester identity
            worker = MediaWorker(worker_id="test-worker-1")
            job.claimed_by = worker.worker_id
            job.status = "running"
            await s.commit()

            with patch("api.workers.media_worker.get_session_factory", return_value=session_factory):
                mock_extractor = MagicMock()
                with patch("api.workers.media_worker.get_clip_extractor", return_value=mock_extractor):
                    await worker._execute_job(job)
                # Verify FFmpeg NEVER ran
                assert not mock_extractor.extract_clip.called

            # Verify job failed non-retryable in fresh session
            async with session_factory() as verify_s:
                stmt = select(MediaWorkItemDB).where(MediaWorkItemDB.id == job.id)
                refreshed = (await verify_s.execute(stmt)).scalar_one()
                assert refreshed.status == "failed"
                assert refreshed.sanitized_error_code in ("HTTPException", "MediaSecurityError")
                assert "not found" in (refreshed.sanitized_error_message or "").lower() or "unauthorized" in (refreshed.sanitized_error_message or "").lower()

    asyncio.run(_test())


def test_02_artifact_ownership_change_before_merge_execution(env_fixture):
    """Member B enqueues a merge job with 2 artifacts. Before worker claims it,

    one artifact is reassigned to Alice. Worker must fail the job.
    """
    session_factory = env_fixture["session_factory"]
    tenant_a = env_fixture["tenant_a"]
    bob_id = env_fixture["bob"]
    alice_id = env_fixture["alice"]

    art1_id = f"art1_{uuid.uuid4().hex[:6]}"
    art2_id = f"art2_{uuid.uuid4().hex[:6]}"

    t_root = env_fixture["t_a_root"]
    c_dir = t_root / "clips"
    (c_dir / f"{art1_id}.mp4").write_bytes(b"clip 1")
    (c_dir / f"{art2_id}.mp4").write_bytes(b"clip 2")

    async def _test():
        async with session_factory() as s:
            s.add_all([
                MediaArtifactDB(
                    id=art1_id,
                    tenant_id=tenant_a,
                    user_id=bob_id,
                    artifact_type="clip",
                    storage_path=f"clips/{art1_id}.mp4",
                    filename=f"{art1_id}.mp4",
                    status="ready",
                ),
                MediaArtifactDB(
                    id=art2_id,
                    tenant_id=tenant_a,
                    user_id=bob_id,
                    artifact_type="clip",
                    storage_path=f"clips/{art2_id}.mp4",
                    filename=f"{art2_id}.mp4",
                    status="ready",
                ),
            ])
            await s.commit()

            bob_user = CurrentUser(
                user_id=bob_id,
                tenant_id=tenant_a,
                role="member",
                auth_method=AuthMethod.JWT,
            )

            job = await submit_merge_job(
                s,
                bob_user,
                clips=[f"art:{art1_id}", f"art:{art2_id}"],
                output_format="mp4",
            )
            assert job.status == "queued"

            # Transfer ownership of art2 to Alice
            stmt = select(MediaArtifactDB).where(MediaArtifactDB.id == art2_id)
            res = await s.execute(stmt)
            art2 = res.scalar_one()
            art2.user_id = alice_id
            await s.commit()

            worker = MediaWorker(worker_id="test-worker-merge")
            job.claimed_by = worker.worker_id
            job.status = "running"
            await s.commit()

            with patch("api.workers.media_worker.get_session_factory", return_value=session_factory):
                mock_merger = MagicMock()
                with patch("api.workers.media_worker.get_clip_merger", return_value=mock_merger):
                    await worker._execute_job(job)
                assert not mock_merger.merge_clips.called

            async with session_factory() as verify_s:
                stmt_j = select(MediaWorkItemDB).where(MediaWorkItemDB.id == job.id)
                refreshed = (await verify_s.execute(stmt_j)).scalar_one()
                assert refreshed.status == "failed"
                assert "not found" in (refreshed.sanitized_error_message or "").lower() or "unauthorized" in (refreshed.sanitized_error_message or "").lower()

    asyncio.run(_test())


def test_03_deactivated_requester_before_claim(env_fixture):
    """Requester user deactivated after job enqueue. Worker revalidation must reject execution."""
    session_factory = env_fixture["session_factory"]
    tenant_a = env_fixture["tenant_a"]
    bob_id = env_fixture["bob"]
    job_id = f"job_deact_{uuid.uuid4().hex[:6]}"

    async def _test():
        async with session_factory() as s:
            job = MediaWorkItemDB(
                id=job_id,
                tenant_id=tenant_a,
                requester_user_id=bob_id,
                operation_type="clip_extract",
                idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
                status="queued",
                request_payload={"source": "downloads/bob_video.mp4"},
            )
            s.add(job)
            await s.commit()

            # Deactivate Bob
            u_stmt = select(UserDB).where(UserDB.id == bob_id)
            bob = (await s.execute(u_stmt)).scalar_one()
            bob.is_active = False
            await s.commit()

            with pytest.raises(MediaSecurityError, match="deactivated or suspended"):
                await revalidate_worker_requester(s, job)

            # Restore Bob
            bob.is_active = True
            await s.commit()

    asyncio.run(_test())


def test_04_revoked_api_key_before_claim(env_fixture):
    """API key revoked after enqueue. Worker revalidation must fail closed."""
    session_factory = env_fixture["session_factory"]
    tenant_a = env_fixture["tenant_a"]
    bob_id = env_fixture["bob"]
    key_id = f"ak_{uuid.uuid4().hex[:8]}"

    async def _test():
        async with session_factory() as s:
            ak = APIKeyDB(
                id=key_id,
                tenant_id=tenant_a,
                user_id=bob_id,
                key_prefix="ak_test",
                key_hash="hash123",
                name="test key",
                permissions=["clips:write"],
                is_active=True,
                is_revoked=False,
            )
            s.add(ak)
            await s.commit()

            job = MediaWorkItemDB(
                id=f"job_ak_{uuid.uuid4().hex[:6]}",
                tenant_id=tenant_a,
                requester_user_id=bob_id,
                requester_api_key_id=key_id,
                operation_type="clip_extract",
                idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
                status="queued",
                request_payload={"source": "downloads/bob_video.mp4"},
            )
            s.add(job)
            await s.commit()

            # Revoke key
            ak.is_revoked = True
            ak.revoked_at = datetime.utcnow()
            await s.commit()

            with pytest.raises(MediaSecurityError, match="has been revoked"):
                await revalidate_worker_requester(s, job)

    asyncio.run(_test())


def test_05_deleted_source_record_before_claim(env_fixture):
    """Source database record deleted before worker claim. Worker must fail non-retryable."""
    session_factory = env_fixture["session_factory"]
    tenant_a = env_fixture["tenant_a"]
    bob_id = env_fixture["bob"]
    dl_id = f"dl_del_{uuid.uuid4().hex[:8]}"

    async def _test():
        async with session_factory() as s:
            dl = DownloadJobDB(
                id=dl_id,
                tenant_id=tenant_a,
                user_id=bob_id,
                url="https://youtube.com/watch?v=del",
                provider="youtube",
                title="to_delete",
                status="completed",
                file_path="downloads/bob_video.mp4",
            )
            s.add(dl)
            await s.commit()

            worker = MediaWorker(worker_id="test-worker-del")
            job = MediaWorkItemDB(
                id=f"job_del_{uuid.uuid4().hex[:6]}",
                tenant_id=tenant_a,
                requester_user_id=bob_id,
                operation_type="clip_extract",
                idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
                status="running",
                claimed_by=worker.worker_id,
                request_payload={"source": f"job:{dl_id}", "canonical_source_id": f"job:{dl_id}"},
            )
            s.add(job)
            await s.commit()

            # Delete download job record from DB
            await s.delete(dl)
            await s.commit()

            with patch("api.workers.media_worker.get_session_factory", return_value=session_factory):
                await worker._execute_job(job)

            async with session_factory() as verify_s:
                stmt_j = select(MediaWorkItemDB).where(MediaWorkItemDB.id == job.id)
                refreshed = (await verify_s.execute(stmt_j)).scalar_one()
                assert refreshed.status == "failed"
                assert "not found" in (refreshed.sanitized_error_message or "").lower()

    asyncio.run(_test())


def test_06_cross_tenant_source_reference_rejected(env_fixture):
    """Requester in Tenant B attempting to execute a job referencing Tenant A media fails closed."""
    session_factory = env_fixture["session_factory"]
    tenant_a = env_fixture["tenant_a"]
    tenant_b = env_fixture["tenant_b"]
    david_id = env_fixture["david"]
    alice_id = env_fixture["alice"]

    dl_id = f"dl_tenant_a_{uuid.uuid4().hex[:8]}"

    async def _test():
        async with session_factory() as s:
            dl = DownloadJobDB(
                id=dl_id,
                tenant_id=tenant_a,
                user_id=alice_id,
                url="https://youtube.com/watch?v=cross",
                provider="youtube",
                title="tenant_a_media",
                status="completed",
                file_path="downloads/bob_video.mp4",
            )
            s.add(dl)
            await s.commit()

            worker = MediaWorker(worker_id="test-worker-cross")
            job = MediaWorkItemDB(
                id=f"job_cross_{uuid.uuid4().hex[:6]}",
                tenant_id=tenant_b,
                requester_user_id=david_id,
                operation_type="clip_extract",
                idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
                status="running",
                claimed_by=worker.worker_id,
                request_payload={"source": f"job:{dl_id}"},
            )
            s.add(job)
            await s.commit()

            with patch("api.workers.media_worker.get_session_factory", return_value=session_factory):
                await worker._execute_job(job)

            async with session_factory() as verify_s:
                stmt_j = select(MediaWorkItemDB).where(MediaWorkItemDB.id == job.id)
                refreshed = (await verify_s.execute(stmt_j)).scalar_one()
                assert refreshed.status == "failed"
                assert "not found" in (refreshed.sanitized_error_message or "").lower()

    asyncio.run(_test())


def test_07_legitimate_tenant_admin_oversight(env_fixture):
    """Tenant Owner/Admin can legitimately reference any member's media in their tenant."""
    session_factory = env_fixture["session_factory"]
    tenant_a = env_fixture["tenant_a"]
    alice_id = env_fixture["alice"]
    bob_id = env_fixture["bob"]
    dl_id = f"dl_bob_owned_{uuid.uuid4().hex[:8]}"

    async def _test():
        async with session_factory() as s:
            dl = DownloadJobDB(
                id=dl_id,
                tenant_id=tenant_a,
                user_id=bob_id,
                url="https://youtube.com/watch?v=admin_test",
                provider="youtube",
                title="bob_owned_file",
                status="completed",
                file_path="downloads/bob_video.mp4",
            )
            s.add(dl)
            await s.commit()

            alice_admin = CurrentUser(
                user_id=alice_id,
                tenant_id=tenant_a,
                role="owner",
                auth_method=AuthMethod.JWT,
            )

            from api.services.artifact_service import resolve_source_media_reference
            resolved = await resolve_source_media_reference(
                s,
                f"job:{dl_id}",
                tenant_a,
                user=alice_admin,
            )
            assert resolved == "downloads/bob_video.mp4"

    asyncio.run(_test())


def test_08_typed_error_classification():
    """Verify is_retryable_error uses typed exceptions and correct transient vs permanent logic."""
    # 1. MediaSecurityError -> Non-retryable
    assert is_retryable_error(MediaSecurityError("Ownership changed")) is False

    # 2. Client HTTPExceptions (400, 401, 403, 404, 409) -> Non-retryable
    assert is_retryable_error(HTTPException(status_code=404, detail="Not found")) is False
    assert is_retryable_error(HTTPException(status_code=403, detail="Forbidden")) is False
    assert is_retryable_error(HTTPException(status_code=409, detail="Conflict")) is False
    assert is_retryable_error(HTTPException(status_code=400, detail="Bad request")) is False

    # 3. Server HTTPExceptions (503, 504) -> Retryable
    assert is_retryable_error(HTTPException(status_code=503, detail="Service unavailable")) is True

    # 4. Network and Timeout errors -> Retryable
    assert is_retryable_error(ConnectionError("Connection reset by peer")) is True
    assert is_retryable_error(asyncio.TimeoutError()) is True
    assert is_retryable_error(TimeoutError("Socket timeout")) is True

    # 5. Database operational errors -> Retryable
    from sqlalchemy.exc import OperationalError
    op_err = OperationalError("connection dropped", {}, Exception("DB down"))
    assert is_retryable_error(op_err) is True
