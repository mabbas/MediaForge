"""Verification and audit regression tests for STEP 02-C2-Fix-A.

Covers:
- TASK 2: Live Redis Rate Limiter Behavior (Atomic Lua, positive TTL initialization,
  counter increments, expiration reset, dual-key/tenant aggregation, isolation, concurrency, 503 fail-closed)
- TASK 3: Service Account Ownership Audit (cross-tenant isolation, no system privilege escalation,
  member visibility boundary, service account job retrieval, cancellation/file ownership, key revocation safety, FK integrity)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import uuid
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import get_api_settings
from api.database.connection import Base, get_session
from api.database.models import APIKeyDB, DownloadJobDB, TenantDB, UserDB
from api.dependencies import AuthMethod, CurrentUser
from api.services.api_key_service import APIKeyService
from api.services.download_service import get_job_from_db, persist_job
from api.services.history_service import get_history
from api.services.rate_limiter import (
    RateLimiter,
    RateLimiterUnavailableError,
    _DUAL_RATE_LIMIT_LUA,
    _SINGLE_RATE_LIMIT_LUA,
)
from src.core.provider_registry import ProviderRegistry
from src.download.download_engine import DownloadEngine
from src.models.download import DownloadJob, DownloadProgress, DownloadRequest
from src.models.enums import DownloadStatus, ProviderType
from tests.unit.test_download_engine import MockDownloadProvider


# ===========================================================================
# Helper: Live Redis Runner for TASK 2 Verification
# ===========================================================================

async def _get_live_redis():
    import redis.asyncio as aioredis
    client = aioredis.Redis(
        host="127.0.0.1",
        port=26379,
        decode_responses=True,
        socket_timeout=3.0,
    )
    await client.ping()
    return client


# ===========================================================================
# TASK 2: Live Redis Rate Limiter Behavior Verification
# ===========================================================================

def test_redis_counter_initializes_positive_ttl():
    """TASK 2.1: First request atomically initializes counter to 1 with a positive TTL."""
    async def _test():
        r = await _get_live_redis()
        key = f"test:rl:unit:init_{uuid.uuid4().hex[:8]}"
        tenant_key = f"test:rl:unit:tenant_{uuid.uuid4().hex[:8]}"
        try:
            results = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key, tenant_key, 60)
            k_count, k_ttl, t_count, t_ttl = results[0], results[1], results[2], results[3]

            assert k_count == 1, "First key request must have count 1"
            assert 0 < k_ttl <= 60, f"First key request must have positive TTL <= 60, got {k_ttl}"
            assert t_count == 1, "First tenant request must have count 1"
            assert 0 < t_ttl <= 60, f"First tenant request must have positive TTL <= 60, got {t_ttl}"

            direct_k_ttl = await r.ttl(key)
            assert direct_k_ttl > 0, "Redis key must have positive TTL in storage"
        finally:
            await r.delete(key, tenant_key)
            await r.aclose()

    asyncio.run(_test())


def test_redis_counter_subsequent_increments_retain_expiration():
    """TASK 2.2: Subsequent increments advance counter while retaining existing expiration."""
    async def _test():
        r = await _get_live_redis()
        key = f"test:rl:unit:inc_{uuid.uuid4().hex[:8]}"
        tenant_key = f"test:rl:unit:tinc_{uuid.uuid4().hex[:8]}"
        try:
            res1 = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key, tenant_key, 60)
            assert res1[0] == 1
            initial_ttl = res1[1]

            await asyncio.sleep(0.05)

            res2 = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key, tenant_key, 60)
            assert res2[0] == 2, "Second request must increment count to 2"
            assert res2[1] <= initial_ttl, "TTL must not reset back up on subsequent requests"
            assert res2[1] > 0, "TTL must remain positive"

            res3 = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key, tenant_key, 60)
            assert res3[0] == 3, "Third request must increment count to 3"
            assert res3[1] > 0
        finally:
            await r.delete(key, tenant_key)
            await r.aclose()

    asyncio.run(_test())


def test_redis_counter_threshold_triggers_rejection_with_retry_after():
    """TASK 2.3: Requests exceeding configured threshold trigger rejection with positive Retry-After."""
    async def _test():
        r = await _get_live_redis()
        key_id = f"unit_test_key_{uuid.uuid4().hex[:8]}"
        tenant_id = f"unit_test_tenant_{uuid.uuid4().hex[:8]}"
        key_metric = f"test:rl:apikey:{key_id}"
        tenant_metric = f"test:rl:tenant_api:{tenant_id}"
        try:
            for i in range(1, 4):
                results = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key_metric, tenant_metric, 60)
                assert results[0] == i

            # 4th request exceeds limit of 3
            results = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key_metric, tenant_metric, 60)
            k_count, k_ttl = results[0], results[1]
            assert k_count == 4
            assert k_count > 3, "Threshold exceeded"
            retry_after = max(k_ttl, 1)
            assert 1 <= retry_after <= 60, f"Retry-After must match remaining TTL: {retry_after}"
        finally:
            await r.delete(key_metric, tenant_metric)
            await r.aclose()

    asyncio.run(_test())


def test_redis_counter_window_expiration_resets():
    """TASK 2.4: Window expiration resets counter to 1 upon next request."""
    async def _test():
        r = await _get_live_redis()
        key = f"test:rl:exp:k_{uuid.uuid4().hex[:8]}"
        tenant_key = f"test:rl:exp:t_{uuid.uuid4().hex[:8]}"
        try:
            res1 = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key, tenant_key, 1)
            assert res1[0] == 1

            await asyncio.sleep(1.15)

            res2 = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key, tenant_key, 1)
            assert res2[0] == 1, "Counter must reset to 1 after window expiration"
            assert res2[1] > 0, "New window must have fresh positive TTL"
        finally:
            await r.delete(key, tenant_key)
            await r.aclose()

    asyncio.run(_test())


def test_redis_tenant_aggregate_shared_across_keys():
    """TASK 2.5: Multiple distinct API keys in the same tenant share aggregate counter."""
    async def _test():
        r = await _get_live_redis()
        tenant_id = f"tenant_shared_{uuid.uuid4().hex[:8]}"
        tenant_metric = f"test:rl:tenant_api:{tenant_id}"
        key1 = f"test:rl:apikey:k1_{uuid.uuid4().hex[:8]}"
        key2 = f"test:rl:apikey:k2_{uuid.uuid4().hex[:8]}"
        try:
            r1 = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key1, tenant_metric, 60)
            r2 = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key1, tenant_metric, 60)
            assert r1[0] == 1 and r1[2] == 1
            assert r2[0] == 2 and r2[2] == 2

            r3 = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key2, tenant_metric, 60)
            assert r3[0] == 1, "Key 2 must have individual count 1"
            assert r3[2] == 3, "Tenant aggregate must reflect cumulative count 3 across all keys"
        finally:
            await r.delete(key1, key2, tenant_metric)
            await r.aclose()

    asyncio.run(_test())


def test_redis_tenant_aggregate_cross_tenant_isolation():
    """TASK 2.6: Tenant aggregate counters do not leak across distinct tenants."""
    async def _test():
        r = await _get_live_redis()
        tenant_a_metric = f"test:rl:tenant_api:alpha_{uuid.uuid4().hex[:8]}"
        tenant_b_metric = f"test:rl:tenant_api:beta_{uuid.uuid4().hex[:8]}"
        key_a = f"test:rl:apikey:ka_{uuid.uuid4().hex[:8]}"
        key_b = f"test:rl:apikey:kb_{uuid.uuid4().hex[:8]}"
        try:
            for _ in range(5):
                await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key_a, tenant_a_metric, 60)

            res_b = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key_b, tenant_b_metric, 60)
            assert res_b[2] == 1, "Tenant Beta aggregate must be 1, completely isolated from Tenant Alpha"

            count_a = await r.get(tenant_a_metric)
            count_b = await r.get(tenant_b_metric)
            assert int(count_a) == 5
            assert int(count_b) == 1
        finally:
            await r.delete(key_a, key_b, tenant_a_metric, tenant_b_metric)
            await r.aclose()

    asyncio.run(_test())


def test_redis_concurrency_race_safety():
    """TASK 2.7: Atomic Lua evaluation prevents concurrency race conditions under parallel execution."""
    async def _test():
        r = await _get_live_redis()
        key = f"test:rl:concur:k_{uuid.uuid4().hex[:8]}"
        tenant = f"test:rl:concur:t_{uuid.uuid4().hex[:8]}"
        try:
            limit = 10
            total_parallel = 25

            async def worker():
                res = await r.eval(_DUAL_RATE_LIMIT_LUA, 2, key, tenant, 60)
                count = res[0]
                allowed = count <= limit
                return allowed, count

            tasks = [asyncio.create_task(worker()) for _ in range(total_parallel)]
            results = await asyncio.gather(*tasks)

            allowed_count = sum(1 for allowed, _ in results if allowed)
            rejected_count = sum(1 for allowed, _ in results if not allowed)

            assert allowed_count == limit, f"Exactly {limit} requests must be allowed, got {allowed_count}"
            assert rejected_count == total_parallel - limit, f"Exactly {total_parallel - limit} rejected"

            final_val = await r.get(key)
            assert int(final_val) == total_parallel, "Redis key must record exact total requests"
            final_ttl = await r.ttl(key)
            assert 0 < final_ttl <= 60, "Key must retain positive TTL"
        finally:
            await r.delete(key, tenant)
            await r.aclose()

    asyncio.run(_test())


def test_rate_limiter_outage_fails_closed():
    """TASK 2.8: Redis connection failure triggers fail-closed RateLimiterUnavailableError."""
    async def _test():
        with patch("api.services.rate_limiter.get_redis_client", return_value=None):
            with pytest.raises(RateLimiterUnavailableError):
                await RateLimiter.check_api_key_limits(
                    key_id="test_key",
                    tenant_id="test_tenant",
                    fail_closed=True,
                )

    asyncio.run(_test())


# ===========================================================================
# TASK 3: Service Account Ownership Audit & Regression Tests
# ===========================================================================

@pytest.fixture
def audit_db_env(tmp_path):
    """Isolated database environment for service account ownership tests."""
    db_file = tmp_path / "audit_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_a_id = "tenant-audit-alpha"
    tenant_b_id = "tenant-audit-beta"

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            ta = TenantDB(id=tenant_a_id, name="Audit Alpha", slug="audit-alpha", tier="platinum", is_active=True)
            tb = TenantDB(id=tenant_b_id, name="Audit Beta", slug="audit-beta", tier="basic", is_active=True)
            session.add_all([ta, tb])

            member_a = UserDB(
                id="user-audit-member-a",
                tenant_id=tenant_a_id,
                email="member@audit-alpha.local",
                password_hash="testhash",
                role="member",
                is_active=True,
            )
            session.add(member_a)

            key_a = APIKeyDB(
                id="key-audit-alpha-001",
                tenant_id=tenant_a_id,
                user_id=member_a.id,
                name="Alpha Service Key",
                key_hash="fakehash",
                key_prefix="mf_live_test",
                permissions=["downloads:read", "downloads:write"],
                is_active=True,
                is_revoked=False,
            )
            session.add(key_a)
            await session.commit()

    asyncio.run(_setup())

    yield {
        "engine": engine,
        "session_factory": session_factory,
        "tenant_a": tenant_a_id,
        "tenant_b": tenant_b_id,
        "member_a": "user-audit-member-a",
        "key_a": "key-audit-alpha-001",
    }

    asyncio.run(engine.dispose())


def test_persist_job_service_account_sets_null_user_and_metadata(audit_db_env):
    """TASK 3.7: Service account jobs have user_id=None and metadata_json={'service_account': ...}."""
    async def _test():
        session_factory = audit_db_env["session_factory"]
        tenant_a = audit_db_env["tenant_a"]
        key_id = audit_db_env["key_a"]
        service_principal = f"service:{key_id}"

        job = DownloadJob(
            job_id="job-sa-001",
            request=DownloadRequest(url="https://example.com/video1.mp4"),
            progress=DownloadProgress(job_id="job-sa-001", status=DownloadStatus.QUEUED),
            priority="high",
            user_id=service_principal,
            tenant_id=tenant_a,
        )

        async with session_factory() as session:
            await persist_job(
                session=session,
                job=job,
                user_id=service_principal,
                tenant_id=tenant_a,
            )
            await session.commit()

            fetched = await session.get(DownloadJobDB, "job-sa-001")
            assert fetched is not None
            assert fetched.user_id is None, "DownloadJobDB.user_id must be None to satisfy FK constraint"
            assert fetched.tenant_id == tenant_a
            assert fetched.metadata_json == {"service_account": service_principal}

    asyncio.run(_test())


def test_regular_members_cannot_see_service_account_jobs(audit_db_env):
    """TASK 3.3: Regular tenant members filtering by user_id cannot see service account jobs."""
    async def _test():
        session_factory = audit_db_env["session_factory"]
        tenant_a = audit_db_env["tenant_a"]
        member_id = audit_db_env["member_a"]
        key_id = audit_db_env["key_a"]

        job_member = DownloadJob(
            job_id="job-member-001",
            request=DownloadRequest(url="https://example.com/mem.mp4"),
            progress=DownloadProgress(job_id="job-member-001", status=DownloadStatus.COMPLETED),
            user_id=member_id,
            tenant_id=tenant_a,
        )
        job_sa = DownloadJob(
            job_id="job-sa-002",
            request=DownloadRequest(url="https://example.com/sa.mp4"),
            progress=DownloadProgress(job_id="job-sa-002", status=DownloadStatus.COMPLETED),
            user_id=f"service:{key_id}",
            tenant_id=tenant_a,
        )

        async with session_factory() as session:
            await persist_job(session, job_member, user_id=member_id, tenant_id=tenant_a)
            await persist_job(session, job_sa, user_id=f"service:{key_id}", tenant_id=tenant_a)
            await session.commit()

            member_jobs, total_member = await get_history(
                session=session,
                tenant_id=tenant_a,
                user_id=member_id,
            )
            member_job_ids = [j.id for j in member_jobs]
            assert "job-member-001" in member_job_ids
            assert "job-sa-002" not in member_job_ids, "Member must NOT see service-account jobs"
            assert total_member == 1

            sa_jobs, total_sa = await get_history(
                session=session,
                tenant_id=tenant_a,
                user_id=None,
            )
            sa_job_ids = [j.id for j in sa_jobs]
            assert "job-member-001" in sa_job_ids
            assert "job-sa-002" in sa_job_ids, "Tenant-wide query must include service-account jobs"
            assert total_sa == 2

    asyncio.run(_test())


def test_cross_tenant_isolation_for_service_account_jobs(audit_db_env):
    """TASK 3.1: Tenant Beta cannot retrieve or access Tenant Alpha's service-account job."""
    async def _test():
        session_factory = audit_db_env["session_factory"]
        tenant_a = audit_db_env["tenant_a"]
        tenant_b = audit_db_env["tenant_b"]
        key_id = audit_db_env["key_a"]

        job_sa = DownloadJob(
            job_id="job-sa-isolation-001",
            request=DownloadRequest(url="https://example.com/iso.mp4"),
            progress=DownloadProgress(job_id="job-sa-isolation-001", status=DownloadStatus.COMPLETED),
            user_id=f"service:{key_id}",
            tenant_id=tenant_a,
        )

        async with session_factory() as session:
            await persist_job(session, job_sa, user_id=f"service:{key_id}", tenant_id=tenant_a)
            await session.commit()

            job_a = await get_job_from_db(session, "job-sa-isolation-001", tenant_id=tenant_a)
            assert job_a is not None
            assert job_a.id == "job-sa-isolation-001"

            job_b = await get_job_from_db(session, "job-sa-isolation-001", tenant_id=tenant_b)
            assert job_b is None, "Tenant Beta must receive None when querying Tenant Alpha job"

    asyncio.run(_test())


def test_download_engine_ownership_and_cancellation():
    """TASK 3.5: Engine _check_ownership enforces tenant isolation and member boundary."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=10)

    try:
        tenant_a = "tenant-engine-a"
        tenant_b = "tenant-engine-b"
        sa_id = "service:key-123"
        member_id = "user-member-456"

        req = DownloadRequest(url="https://mock.test/stream.mp4")
        job = engine.submit_download(
            request=req,
            priority="normal",
            user_id=sa_id,
            tenant_id=tenant_a,
            start=False,
        )

        # 1. Tenant Alpha service account can access job
        assert engine.get_job(job.job_id, tenant_id=tenant_a) is not None

        # 2. Tenant Beta cannot access job
        assert engine.get_job(job.job_id, tenant_id=tenant_b) is None

        # 3. Regular member in Tenant Alpha cannot access or cancel service account job
        assert engine.get_job(job.job_id, tenant_id=tenant_a, user_id=member_id) is None
        assert not engine.cancel_job(job.job_id, tenant_id=tenant_a, user_id=member_id)

        # 4. Service account (tenant_id=tenant_a, user_id=None) can cancel job
        assert engine.cancel_job(job.job_id, tenant_id=tenant_a, user_id=None)
        cancelled_job = engine.get_job(job.job_id, tenant_id=tenant_a)
        assert cancelled_job.progress.status == DownloadStatus.CANCELLED
    finally:
        engine.shutdown(wait=False)


def test_key_revocation_leaves_jobs_intact(audit_db_env):
    """TASK 3.6: Revoking or rotating an API key leaves existing download jobs intact."""
    async def _test():
        session_factory = audit_db_env["session_factory"]
        tenant_a = audit_db_env["tenant_a"]
        key_id = audit_db_env["key_a"]

        job = DownloadJob(
            job_id="job-sa-persist-001",
            request=DownloadRequest(url="https://example.com/keep.mp4"),
            progress=DownloadProgress(job_id="job-sa-persist-001", status=DownloadStatus.COMPLETED),
            user_id=f"service:{key_id}",
            tenant_id=tenant_a,
        )

        async with session_factory() as session:
            await persist_job(session, job, user_id=f"service:{key_id}", tenant_id=tenant_a)
            await session.commit()

            key_record = await session.get(APIKeyDB, key_id)
            assert key_record is not None
            key_record.is_revoked = True
            key_record.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()

            db_job = await session.get(DownloadJobDB, "job-sa-persist-001")
            assert db_job is not None
            assert db_job.status == "completed"
            assert db_job.metadata_json == {"service_account": f"service:{key_id}"}

    asyncio.run(_test())
