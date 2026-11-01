"""Unit tests for durable background jobs, worker lifecycle, and isolation (STEP 03-B).

Verifies:
1. Job submission validation and idempotency handling (returns existing job on duplicate).
2. Cross-tenant anti-enumeration (404 on cross-tenant job queries or cancellations).
3. Intra-tenant member isolation (members cannot view other members' jobs; admins can).
4. Cancellation mechanics (immediate for queued jobs; cancellation_requested_at for running).
5. Stale worker orphan recovery with recover_expired_leases.
6. REST API contracts for /api/v1/jobs/clips and /api/v1/jobs/{job_id}.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base, get_session
from api.database.models import MediaWorkItemDB, TenantDB, UserDB
from api.dependencies import CurrentUser
from api.security.jwt import create_access_token
from api.schemas.clips import ClipExtractRequest
from api.services.job_service import (
    complete_job_and_artifact_atomic,
    get_authorized_job,
    recover_expired_leases,
    request_job_cancellation,
    submit_clip_job,
)


@pytest.fixture
def jobs_env(tmp_path, monkeypatch):
    """Set up isolated multi-tenant database environment for background jobs."""
    storage_root = tmp_path / "media_storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GID_DOWNLOAD__OUTPUT_DIRECTORY", str(storage_root))

    db_file = tmp_path / "jobs_unit_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("GID_API_DATABASE_URL", db_url)
    monkeypatch.setenv("GID_JWT_SECRET_KEY", "jobs-test-secret-key-at-least-32-bytes!!")
    monkeypatch.setenv("GID_API_SKIP_SEED", "1")

    from api.config import get_api_settings
    get_api_settings.cache_clear()

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_a_id = "tenant-alpha"
    tenant_b_id = "tenant-beta"

    async def _setup_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            t_a = TenantDB(id=tenant_a_id, name="Alpha Corp", slug="alpha", is_active=True)
            t_b = TenantDB(id=tenant_b_id, name="Beta Corp", slug="beta", is_active=True)
            session.add_all([t_a, t_b])

            u_admin = UserDB(
                id="user-a-admin",
                tenant_id=tenant_a_id,
                email="admin@alpha.test",
                role="admin",
                is_active=True,
                password_hash="hash",
            )
            u_mem1 = UserDB(
                id="user-a-mem1",
                tenant_id=tenant_a_id,
                email="m1@alpha.test",
                role="member",
                is_active=True,
                password_hash="hash",
            )
            u_mem2 = UserDB(
                id="user-a-mem2",
                tenant_id=tenant_a_id,
                email="m2@alpha.test",
                role="member",
                is_active=True,
                password_hash="hash",
            )
            u_b_owner = UserDB(
                id="user-b-owner",
                tenant_id=tenant_b_id,
                email="owner@beta.test",
                role="owner",
                is_active=True,
                password_hash="hash",
            )
            session.add_all([u_admin, u_mem1, u_mem2, u_b_owner])
            await session.commit()

    asyncio.run(_setup_db())

    from api.main import app

    async def _mock_get_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _mock_get_session

    users = {
        "admin": CurrentUser(user_id="user-a-admin", tenant_id=tenant_a_id, role="admin", email="admin@alpha.test", is_active=True, scopes=["clips:read", "clips:write"]),
        "mem1": CurrentUser(user_id="user-a-mem1", tenant_id=tenant_a_id, role="member", email="m1@alpha.test", is_active=True, scopes=["clips:read", "clips:write"]),
        "mem2": CurrentUser(user_id="user-a-mem2", tenant_id=tenant_a_id, role="member", email="m2@alpha.test", is_active=True, scopes=["clips:read", "clips:write"]),
        "beta": CurrentUser(user_id="user-b-owner", tenant_id=tenant_b_id, role="owner", email="owner@beta.test", is_active=True, scopes=["clips:read", "clips:write"]),
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
        }

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def test_job_idempotency_service_level(jobs_env):
    """Test submit_clip_job service returns identical record for duplicate idempotency key."""
    session_factory = jobs_env["session_factory"]
    user_m1 = jobs_env["users"]["mem1"]

    async def _run():
        async with session_factory() as db:
            with patch("api.services.job_service.verify_source_media_authorization", return_value=None), \
                 patch("api.services.job_service.resolve_and_authorize_clip_source", return_value=Path("/tmp/fake.mp4")), \
                 patch("api.services.job_service.notify_new_job_event", return_value=None):

                req = ClipExtractRequest(
                    source="downloads/fake.mp4",
                    start_time="00:00:01.000",
                    end_time="00:00:03.000",
                    mode="precise",
                    output_format="mp4",
                )

                job1 = await submit_clip_job(db, user_m1, req, idempotency_key="idemp_key_123")
                assert job1.id is not None
                assert job1.status == "queued"
                assert job1.idempotency_key == "idemp_key_123"

                job2 = await submit_clip_job(db, user_m1, req, idempotency_key="idemp_key_123")
                assert job2.id == job1.id

    asyncio.run(_run())


def test_cross_tenant_anti_enumeration(jobs_env):
    """Test get_authorized_job raises 404 for cross-tenant job."""
    session_factory = jobs_env["session_factory"]
    user_beta = jobs_env["users"]["beta"]

    async def _run():
        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            job_a = MediaWorkItemDB(
                id="job-alpha-001",
                tenant_id=jobs_env["tenant_a_id"],
                requester_user_id="user-a-mem1",
                operation_type="clip_extract",
                idempotency_key="clip:key_a",
                request_payload={"source": "test.mp4"},
                status="queued",
                created_at=now,
                updated_at=now,
            )
            db.add(job_a)
            await db.commit()

            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await get_authorized_job(db, "job-alpha-001", user_beta)

            assert exc_info.value.status_code == 404
            assert exc_info.value.detail == "Job not found"

    asyncio.run(_run())


def test_intra_tenant_member_isolation(jobs_env):
    """Test that a member cannot inspect another member's job, but admin can."""
    session_factory = jobs_env["session_factory"]
    user_m2 = jobs_env["users"]["mem2"]
    user_admin = jobs_env["users"]["admin"]

    async def _run():
        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            job_m1 = MediaWorkItemDB(
                id="job-alpha-m1",
                tenant_id=jobs_env["tenant_a_id"],
                requester_user_id="user-a-mem1",
                operation_type="clip_extract",
                idempotency_key="clip:key_m1",
                request_payload={"source": "test.mp4"},
                status="queued",
                created_at=now,
                updated_at=now,
            )
            db.add(job_m1)
            await db.commit()

            from fastapi import HTTPException
            # Member 2 cannot access Member 1's job -> 404
            with pytest.raises(HTTPException) as exc_info:
                await get_authorized_job(db, "job-alpha-m1", user_m2)
            assert exc_info.value.status_code == 404

            # Admin can access Member 1's job
            item = await get_authorized_job(db, "job-alpha-m1", user_admin)
            assert item.id == "job-alpha-m1"

    asyncio.run(_run())


def test_cancellation_flow(jobs_env):
    """Test cancellation transitions for queued and running jobs."""
    session_factory = jobs_env["session_factory"]
    user_m1 = jobs_env["users"]["mem1"]

    async def _run():
        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            job_q = MediaWorkItemDB(
                id="job-q-1",
                tenant_id=jobs_env["tenant_a_id"],
                requester_user_id="user-a-mem1",
                operation_type="clip_extract",
                idempotency_key="clip:q1",
                request_payload={},
                status="queued",
                created_at=now,
                updated_at=now,
            )
            job_r = MediaWorkItemDB(
                id="job-r-1",
                tenant_id=jobs_env["tenant_a_id"],
                requester_user_id="user-a-mem1",
                operation_type="clip_extract",
                idempotency_key="clip:r1",
                request_payload={},
                status="running",
                claimed_by="worker-test",
                created_at=now,
                updated_at=now,
            )
            db.add_all([job_q, job_r])
            await db.commit()

            # Queued job cancels immediately
            item_q, msg_q = await request_job_cancellation(db, "job-q-1", user_m1)
            assert item_q.status == "cancelled"
            assert "immediately" in msg_q

            # Running job sets cancellation_requested_at
            item_r, msg_r = await request_job_cancellation(db, "job-r-1", user_m1)
            assert item_r.status == "running"
            assert item_r.cancellation_requested_at is not None
            assert "running" in msg_r

    asyncio.run(_run())


def test_stale_worker_lease_recovery(jobs_env):
    """Test recover_expired_leases reclaims expired running jobs into retry_wait."""
    session_factory = jobs_env["session_factory"]

    async def _run():
        async with session_factory() as db:
            past = datetime.now(timezone.utc) - timedelta(minutes=5)
            stale = MediaWorkItemDB(
                id="job-stale-99",
                tenant_id=jobs_env["tenant_a_id"],
                requester_user_id="user-a-mem1",
                operation_type="clip_extract",
                idempotency_key="clip:stale99",
                request_payload={},
                status="running",
                claimed_by="crashed-worker",
                lease_expires_at=past,
                attempt_count=1,
                max_attempts=3,
                created_at=past,
                updated_at=past,
            )
            db.add(stale)
            await db.commit()

            reclaimed = await recover_expired_leases(db)
            assert reclaimed == 1

            stmt = select(MediaWorkItemDB).where(MediaWorkItemDB.id == "job-stale-99")
            res = await db.execute(stmt)
            job = res.scalar_one()

            assert job.status == "retry_wait"
            assert job.claimed_by is None
            assert job.attempt_count == 1
            assert "lease expired" in (job.sanitized_error_message or "").lower()

    asyncio.run(_run())


def test_api_jobs_rest_endpoints(jobs_env):
    """Test GET /api/v1/jobs and POST /api/v1/jobs/{job_id}/cancel via TestClient."""
    client = jobs_env["client"]
    tokens = jobs_env["tokens"]
    session_factory = jobs_env["session_factory"]

    # Seed a job
    async def _seed():
        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            item = MediaWorkItemDB(
                id="job-rest-01",
                tenant_id=jobs_env["tenant_a_id"],
                requester_user_id="user-a-mem1",
                operation_type="clip_extract",
                idempotency_key="clip:rest01",
                request_payload={"source": "video.mp4"},
                status="queued",
                created_at=now,
                updated_at=now,
            )
            db.add(item)
            await db.commit()

    asyncio.run(_seed())

    # Member 1 queries their job -> 200 OK
    headers_m1 = {"Authorization": f"Bearer {tokens['mem1']}"}
    r = client.get("/api/v1/jobs/job-rest-01", headers=headers_m1)
    assert r.status_code == 200
    assert r.json()["job_id"] == "job-rest-01"
    assert r.json()["status"] == "queued"

    # Member 2 queries Member 1's job -> 404 Not Found
    headers_m2 = {"Authorization": f"Bearer {tokens['mem2']}"}
    r_m2 = client.get("/api/v1/jobs/job-rest-01", headers=headers_m2)
    assert r_m2.status_code == 404

    # Beta tenant queries Alpha's job -> 404 Not Found
    headers_beta = {"Authorization": f"Bearer {tokens['beta']}"}
    r_beta = client.get("/api/v1/jobs/job-rest-01", headers=headers_beta)
    assert r_beta.status_code == 404

    # Cancel job via REST API -> 200 OK
    r_cancel = client.post("/api/v1/jobs/job-rest-01/cancel", headers=headers_m1)
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "cancelled"


def test_job_idempotency_conflict_detection(jobs_env):
    """Verify HTTP 409 Conflict when idempotency key is reused with differing payloads."""
    from fastapi import HTTPException

    session_factory = jobs_env["session_factory"]
    user_m1 = jobs_env["users"]["mem1"]

    async def _run():
        async with session_factory() as db:
            with patch("api.services.job_service.verify_source_media_authorization", return_value=None), \
                 patch("api.services.job_service.resolve_and_authorize_clip_source", return_value=Path("/tmp/fake.mp4")), \
                 patch("api.services.job_service.notify_new_job_event", return_value=None):

                # First submission
                req1 = ClipExtractRequest(
                    source="downloads/fake.mp4",
                    start_time="00:00:01",
                    end_time="00:00:03",
                    mode="precise",
                    output_format="mp4",
                )
                item1 = await submit_clip_job(
                    db,
                    user_m1,
                    req1,
                    idempotency_key="key-conflict-test",
                )
                assert item1.id is not None

                # Replay identical payload -> returns item1
                item2 = await submit_clip_job(
                    db,
                    user_m1,
                    req1,
                    idempotency_key="key-conflict-test",
                )
                assert item2.id == item1.id

                # Conflicting payload with different end_time -> raises HTTP 409
                req_diff = ClipExtractRequest(
                    source="downloads/fake.mp4",
                    start_time="00:00:01",
                    end_time="00:00:05",
                    mode="precise",
                    output_format="mp4",
                )
                with pytest.raises(HTTPException) as exc_info:
                    await submit_clip_job(
                        db,
                        user_m1,
                        req_diff,
                        idempotency_key="key-conflict-test",
                    )
                assert exc_info.value.status_code == 409
                assert "Idempotency conflict" in exc_info.value.detail
                assert "end_time" in exc_info.value.detail

    asyncio.run(_run())


def test_atomic_fencing_completion(jobs_env):
    """Verify complete_job_and_artifact_atomic prevents fenced/stale worker completion."""
    from api.database.models import MediaArtifactDB, UsageEventDB

    session_factory = jobs_env["session_factory"]
    tenant_id = jobs_env["tenant_a_id"]
    now = datetime.now(timezone.utc)

    async def _run():
        async with session_factory() as db:
            # Seed job and artifact
            job = MediaWorkItemDB(
                id="job-fence-01",
                tenant_id=tenant_id,
                requester_user_id="user-a-mem1",
                operation_type="clip_extract",
                idempotency_key="key-fence-01",
                status="running",
                attempt_count=1,
                max_attempts=3,
                claimed_by="worker-A",
                started_at=now,
                lease_expires_at=now + timedelta(seconds=30),
                available_at=now,
                progress_percent=50.0,
                created_at=now,
                updated_at=now,
            )
            art = MediaArtifactDB(
                id="art-fence-01",
                tenant_id=tenant_id,
                user_id="user-a-mem1",
                artifact_type="clip",
                storage_path="clips/fence.mp4",
                filename="fence.mp4",
                format="mp4",
                status="processing",
                created_at=now,
                updated_at=now,
            )
            db.add_all([job, art])
            await db.commit()

        usage = {
            "tenant_id": tenant_id,
            "event_type": "clip",
            "idempotency_key": "clip:fence-01",
            "resource_id": "art-fence-01",
            "quantity": 1.0,
            "unit": "count",
            "bytes": 2048,
            "duration_seconds": 2.0,
            "user_id": "user-a-mem1",
            "auth_method": "worker",
            "metadata_json": {},
        }

        # 1. Stale worker B tries to complete job claimed by worker A -> False
        async with session_factory() as db:
            fenced = await complete_job_and_artifact_atomic(
                db=db,
                job_id="job-fence-01",
                worker_id="worker-B",
                artifact_id="art-fence-01",
                duration_seconds=2.0,
                file_size_bytes=2048,
                sha256_checksum="abc",
                usage_event_kwargs=usage,
                expected_attempt=1,
            )
            assert fenced is False

        # 2. Worker A with wrong attempt count -> False
        async with session_factory() as db:
            fenced = await complete_job_and_artifact_atomic(
                db=db,
                job_id="job-fence-01",
                worker_id="worker-A",
                artifact_id="art-fence-01",
                duration_seconds=2.0,
                file_size_bytes=2048,
                sha256_checksum="abc",
                usage_event_kwargs=usage,
                expected_attempt=2,
            )
            assert fenced is False

        # 3. Worker A with correct attempt count -> True
        async with session_factory() as db:
            success = await complete_job_and_artifact_atomic(
                db=db,
                job_id="job-fence-01",
                worker_id="worker-A",
                artifact_id="art-fence-01",
                duration_seconds=2.0,
                file_size_bytes=2048,
                sha256_checksum="abc",
                usage_event_kwargs=usage,
                expected_attempt=1,
            )
            assert success is True

        # Verify state in DB
        async with session_factory() as db:
            stmt_job = select(MediaWorkItemDB).where(MediaWorkItemDB.id == "job-fence-01")
            j = (await db.execute(stmt_job)).scalar_one()
            assert j.status == "succeeded"
            assert j.result_artifact_id == "art-fence-01"

            stmt_art = select(MediaArtifactDB).where(MediaArtifactDB.id == "art-fence-01")
            a = (await db.execute(stmt_art)).scalar_one()
            assert a.status == "ready"
            assert a.sha256_checksum == "abc"

            stmt_u = select(UsageEventDB).where(UsageEventDB.idempotency_key == "clip:fence-01")
            u = (await db.execute(stmt_u)).scalar_one()
            assert u.bytes == 2048

        # 4. Attempting to complete already-succeeded job -> False
        async with session_factory() as db:
            fenced = await complete_job_and_artifact_atomic(
                db=db,
                job_id="job-fence-01",
                worker_id="worker-A",
                artifact_id="art-fence-01",
                duration_seconds=2.0,
                file_size_bytes=2048,
                sha256_checksum="abc",
                usage_event_kwargs=usage,
                expected_attempt=1,
            )
            assert fenced is False

    asyncio.run(_run())


def test_publish_artifact_file_no_clobber_and_publication_ordering(tmp_path: Path):
    """Verify safe artifact publication, no-clobber invariants, and publication-before-commit (STEP 03-B-Fix-C)."""
    import hashlib
    from api.security.media_storage import (
        publish_artifact_file_no_clobber,
        PublicationConflictError,
        compute_file_sha256,
    )

    staging = tmp_path / "temp" / "staging"
    clips = tmp_path / "clips"
    staging.mkdir(parents=True, exist_ok=True)
    clips.mkdir(parents=True, exist_ok=True)

    # 1. Normal publication: staging promoted to output and unlinked
    s1 = staging / "stage_job1_att1_token1.mp4"
    d1 = clips / "clip_art1.mp4"
    data1 = b"video-frame-bytes-111"
    s1.write_bytes(data1)
    h1 = hashlib.sha256(data1).hexdigest()

    ok = publish_artifact_file_no_clobber(s1, d1, h1, len(data1))
    assert ok is True
    assert d1.exists()
    assert not s1.exists()
    assert d1.read_bytes() == data1
    assert compute_file_sha256(d1) == h1

    # 2. Idempotent publication of identical content (Case 8 retry scenario)
    s2 = staging / "stage_job1_att2_token2.mp4"
    s2.write_bytes(data1)
    ok2 = publish_artifact_file_no_clobber(s2, d1, h1, len(data1))
    assert ok2 is True
    assert d1.exists()
    assert not s2.exists()
    assert d1.read_bytes() == data1

    # 3. Competing attempt with different content raises PublicationConflictError
    s3 = staging / "stage_job1_att3_token3.mp4"
    data3 = b"competing-conflicting-bytes-333"
    s3.write_bytes(data3)
    h3 = hashlib.sha256(data3).hexdigest()

    conflict_raised = False
    try:
        publish_artifact_file_no_clobber(s3, d1, h3, len(data3))
    except PublicationConflictError:
        conflict_raised = True

    assert conflict_raised is True
    # Invariant: Authoritative output was 100% preserved and NOT overwritten!
    assert d1.read_bytes() == data1
    assert compute_file_sha256(d1) == h1
    assert s3.exists()


def test_exdev_failure_fails_closed_without_overwriting_or_usage(jobs_env, tmp_path: Path):
    """Verify that an EXDEV cross-device link failure fails closed, rolls back DB, and records zero usage (TASK 1)."""
    import hashlib
    import os
    from unittest.mock import patch
    from api.security.media_storage import (
        publish_artifact_file_no_clobber,
        MediaSecurityError,
        compute_file_sha256,
    )
    from api.database.models import MediaWorkItemDB, MediaArtifactDB, UsageEventDB
    from api.services.job_service import complete_job_and_artifact_atomic

    session_factory = jobs_env["session_factory"]
    tenant_id = jobs_env["tenant_a_id"]

    staging = tmp_path / "temp" / "staging"
    clips = tmp_path / "clips"
    staging.mkdir(parents=True, exist_ok=True)
    clips.mkdir(parents=True, exist_ok=True)

    # Pre-existing winning authoritative output
    d_out = clips / "clip_exdev.mp4"
    winning_data = b"winning-prior-file"
    d_out.write_bytes(winning_data)
    winning_hash = hashlib.sha256(winning_data).hexdigest()

    # Competing staging attempt
    s_attempt = staging / "stage_job_exdev_att1.mp4"
    staged_data = b"new-attempt-data"
    s_attempt.write_bytes(staged_data)
    staged_hash = hashlib.sha256(staged_data).hexdigest()

    # Destination that doesn't exist yet to test the os.link branch
    d_new = clips / "clip_new_target.mp4"

    # Simulate EXDEV (errno 18) on os.link
    exdev_err = OSError()
    exdev_err.errno = 18
    exdev_err.strerror = "Invalid cross-device link"

    with patch("os.link", side_effect=exdev_err):
        # 1. Direct publication call raises MediaSecurityError (fail closed)
        exdev_caught = False
        try:
            publish_artifact_file_no_clobber(s_attempt, d_new, staged_hash, len(staged_data))
        except MediaSecurityError as err:
            exdev_caught = True
            assert "EXDEV" in str(err)
        assert exdev_caught is True
        assert not d_new.exists(), "Target file must not be created on EXDEV failure"

        # 2. In-transaction atomic completion rollback test
        async def _run():
            from datetime import datetime

            async with session_factory() as db:
                item = MediaWorkItemDB(
                    id="job-exdev-01",
                    tenant_id=tenant_id,
                    operation_type="clip_extract",
                    idempotency_key="idem:job-exdev-01",
                    status="running",
                    attempt_count=1,
                    claimed_by="worker-exdev",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                art = MediaArtifactDB(
                    id="art-exdev-01",
                    tenant_id=tenant_id,
                    artifact_type="clip",
                    storage_path="clips/clip_new_target.mp4",
                    filename="clip_new_target.mp4",
                    status="processing",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add_all([item, art])
                await db.commit()

            usage = {
                "tenant_id": tenant_id,
                "event_type": "clip",
                "idempotency_key": "clip:exdev-01",
                "resource_id": "art-exdev-01",
                "quantity": 1.0,
                "unit": "count",
                "bytes": len(staged_data),
                "duration_seconds": 2.0,
            }

            # Attempt atomic completion with EXDEV happening during publication
            async with session_factory() as db:
                success = await complete_job_and_artifact_atomic(
                    db=db,
                    job_id="job-exdev-01",
                    worker_id="worker-exdev",
                    artifact_id="art-exdev-01",
                    duration_seconds=2.0,
                    file_size_bytes=len(staged_data),
                    sha256_checksum=staged_hash,
                    usage_event_kwargs=usage,
                    expected_attempt=1,
                    staging_path=s_attempt,
                    abs_output=d_new,
                )
                assert success is False, "complete_job_and_artifact_atomic must return False on EXDEV"

            # Verify DB state: rolled back, NOT succeeded, 0 usage events
            async with session_factory() as db:
                j = (await db.execute(select(MediaWorkItemDB).where(MediaWorkItemDB.id == "job-exdev-01"))).scalar_one()
                a = (await db.execute(select(MediaArtifactDB).where(MediaArtifactDB.id == "art-exdev-01"))).scalar_one()
                u = (await db.execute(select(UsageEventDB).where(UsageEventDB.idempotency_key == "clip:exdev-01"))).scalar_one_or_none()
                assert j.status == "running", "Job must remain in running state"
                assert a.status == "processing", "Artifact must remain in processing state"
                assert u is None, "Zero usage events must be ledgered on publication failure"

            # Verify pre-existing winning file is untouched
            assert d_out.read_bytes() == winning_data
            assert compute_file_sha256(d_out) == winning_hash

        import asyncio
        asyncio.run(_run())
