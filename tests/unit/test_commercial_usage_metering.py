"""Unit and integration tests for commercial usage metering, ledger tracking, and advisory quotas (STEP 02-E2B)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base, get_session
from api.database.models import APIKeyDB, TenantDB, TenantQuotaDB, UsageEventDB, UserDB
from api.dependencies import AuthMethod, CurrentUser
from api.main import app
from api.security.jwt import create_access_token
from api.security.api_keys import generate_api_key, hash_api_key
from api.services.usage_service import (
    get_tenant_advisory_quotas,
    get_tenant_usage_events,
    get_tenant_usage_summary,
    record_usage_event,
)


@pytest.fixture
def usage_env(tmp_path, monkeypatch):
    """Set up isolated multi-tenant database environment for usage testing."""
    db_file = tmp_path / "usage_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("GID_API_DATABASE_URL", db_url)
    monkeypatch.setenv("GID_JWT_SECRET_KEY", "usage-test-secret-key-at-least-32-bytes!!")
    monkeypatch.setenv("GID_API_SKIP_SEED", "1")

    from api.config import get_api_settings
    get_api_settings.cache_clear()

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    import api.services.usage_service as usg_svc
    monkeypatch.setattr(usg_svc, "async_session_factory", session_factory)

    async def _setup_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup_db())

    async def _mock_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _mock_get_session

    client = TestClient(app)
    yield client, session_factory

    app.dependency_overrides.pop(get_session, None)
    asyncio.run(engine.dispose())


def test_usage_event_idempotency_enforcement(usage_env):
    """Database-enforced idempotency prevents duplicate usage event insertion."""
    _, session_factory = usage_env
    tenant_id = str(uuid.uuid4())

    async def _test():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Idempotency Org", slug=f"idemp-{uuid.uuid4().hex[:6]}")
            session.add(tenant)
            await session.commit()

            idempotency_key = f"clip:test_clip_{uuid.uuid4().hex[:8]}"

            # 1. First emission: should succeed as new
            event1, is_new1 = await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key=idempotency_key,
                resource_id="clip_123",
                quantity=1.0,
                unit="count",
                bytes=50000,
                duration_seconds=2.5,
            )
            await session.commit()
            assert is_new1 is True
            assert event1.idempotency_key == idempotency_key

            # 2. Second emission with exact same tenant and idempotency_key: should be deduplicated
            event2, is_new2 = await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key=idempotency_key,
                resource_id="clip_123",
                quantity=1.0,
                unit="count",
                bytes=50000,
                duration_seconds=2.5,
            )
            await session.commit()
            assert is_new2 is False
            assert event2.id == event1.id

            # 3. Verify exactly one row exists in database
            stmt = select(UsageEventDB).where(
                UsageEventDB.tenant_id == tenant_id,
                UsageEventDB.idempotency_key == idempotency_key,
            )
            rows = (await session.execute(stmt)).scalars().all()
            assert len(rows) == 1

    asyncio.run(_test())


def test_usage_attribution_user_and_api_key(usage_env):
    """Events properly attribute tenant, user, and API key identifiers."""
    _, session_factory = usage_env
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    async def _test():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Attribution Org", slug=f"attr-{uuid.uuid4().hex[:6]}")
            user = UserDB(id=user_id, tenant_id=tenant_id, email=f"user-{uuid.uuid4().hex[:6]}@example.com")
            api_key = APIKeyDB(
                id=key_id,
                tenant_id=tenant_id,
                user_id=user_id,
                name="Test Commercial Key",
                key_prefix="mf_live_test1234",
                key_hash=uuid.uuid4().hex,
                permissions=["downloads:read", "usage:read"],
            )
            session.add_all([tenant, user, api_key])
            await session.commit()

            # JWT Session Attribution
            session_event, _ = await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="download",
                idempotency_key=f"dl:jwt_{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                api_key_id=None,
                auth_method="jwt",
                bytes=1048576,
            )

            # API Key Attribution
            key_event, _ = await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key=f"clip:key_{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                api_key_id=key_id,
                auth_method="api_key",
                bytes=204800,
            )
            await session.commit()

            assert session_event.user_id == user_id
            assert session_event.api_key_id is None
            assert session_event.auth_method == "jwt"

            assert key_event.user_id == user_id
            assert key_event.api_key_id == key_id
            assert key_event.auth_method == "api_key"

    asyncio.run(_test())


def test_operation_consumption_measurements(usage_env):
    """Accurate measurement across downloads, clips, merges, and transcripts."""
    _, session_factory = usage_env
    tenant_id = str(uuid.uuid4())

    async def _test():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Metrics Org", slug=f"metr-{uuid.uuid4().hex[:6]}")
            session.add(tenant)
            await session.commit()

            # 1. Download
            await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="download",
                idempotency_key="dl_1",
                resource_id="job_dl_1",
                quantity=1.0,
                unit="count",
                bytes=1000000,
                duration_seconds=60.0,
            )

            # 2. Clip
            await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key="clip_1",
                resource_id="art_clip_1",
                quantity=1.0,
                unit="count",
                bytes=200000,
                duration_seconds=15.0,
            )

            # 3. Merge
            await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="merge",
                idempotency_key="merge_1",
                resource_id="art_merge_1",
                quantity=1.0,
                unit="count",
                bytes=400000,
                duration_seconds=30.0,
            )

            # 4. Transcription
            await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="transcription",
                idempotency_key="tr_1",
                resource_id="tr_123",
                quantity=2.5,
                unit="minutes",
                bytes=0,
                duration_seconds=150.0,
            )
            await session.commit()

            summary = await get_tenant_usage_summary(session, tenant_id=tenant_id)
            totals = summary["totals"]
            assert totals["total_events"] == 4
            assert totals["total_bytes"] == 1600000
            assert totals["downloads_count"] == 1
            assert totals["download_bytes"] == 1000000
            assert totals["clips_count"] == 1
            assert totals["clip_seconds"] == 15.0
            assert totals["merges_count"] == 1
            assert totals["merge_seconds"] == 30.0
            assert totals["transcriptions_count"] == 1
            assert totals["transcription_minutes"] == 2.5

    asyncio.run(_test())


def test_advisory_quotas_unlimited_default(usage_env):
    """Advisory quotas default to UNLIMITED (-1) with zero artificial blocking."""
    _, session_factory = usage_env
    tenant_id = str(uuid.uuid4())

    async def _test():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Quota Org", slug=f"quota-{uuid.uuid4().hex[:6]}")
            session.add(tenant)
            await session.commit()

            # Emit some usage
            await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key=f"clip:{uuid.uuid4().hex[:8]}",
                bytes=150000,
                duration_seconds=5.0,
            )
            await session.commit()

            report = await get_tenant_advisory_quotas(session, tenant_id=tenant_id, period="monthly")
            quotas = report["quotas"]

            # All standard metrics must report limit=-1 (UNLIMITED) and is_exceeded=False
            for metric_name in ("downloads", "download_bytes", "clips", "clip_seconds", "merges", "transcription_minutes"):
                assert metric_name in quotas
                q = quotas[metric_name]
                assert q["limit"] == -1
                assert q["remaining"] == -1
                assert q["is_exceeded"] is False
                assert q["is_hard_limit"] is False

            assert quotas["clips"]["used"] == 1

    asyncio.run(_test())


def test_custom_quota_evaluation(usage_env):
    """Configured quotas correctly compute used, remaining, and is_exceeded."""
    _, session_factory = usage_env
    tenant_id = str(uuid.uuid4())

    async def _test():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Custom Quota Org", slug=f"cq-{uuid.uuid4().hex[:6]}")
            # Configure a custom quota limit of 2 clips
            quota = TenantQuotaDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                metric="clips",
                period="monthly",
                limit_value=2,
                is_hard_limit=True,
            )
            session.add_all([tenant, quota])
            await session.commit()

            # 1. Emit 1 clip -> not exceeded, remaining 1
            await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key="clip_c1",
                quantity=1.0,
            )
            await session.commit()

            rep1 = await get_tenant_advisory_quotas(session, tenant_id=tenant_id, period="monthly")
            q1 = rep1["quotas"]["clips"]
            assert q1["used"] == 1
            assert q1["limit"] == 2
            assert q1["remaining"] == 1
            assert q1["is_exceeded"] is False
            assert q1["is_hard_limit"] is True

            # 2. Emit second clip -> limit reached (used=2, remaining=0, is_exceeded=True)
            await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key="clip_c2",
                quantity=1.0,
            )
            await session.commit()

            rep2 = await get_tenant_advisory_quotas(session, tenant_id=tenant_id, period="monthly")
            q2 = rep2["quotas"]["clips"]
            assert q2["used"] == 2
            assert q2["limit"] == 2
            assert q2["remaining"] == 0
            assert q2["is_exceeded"] is True

    asyncio.run(_test())


def test_tenant_isolation_usage_endpoints(usage_env):
    """Cross-tenant access to usage reports is strictly blocked with HTTP 403."""
    client, session_factory = usage_env
    tenant_a_id = str(uuid.uuid4())
    tenant_b_id = str(uuid.uuid4())
    user_a_id = str(uuid.uuid4())

    async def _setup():
        async with session_factory() as session:
            tenant_a = TenantDB(id=tenant_a_id, name="Org A", slug=f"org-a-{uuid.uuid4().hex[:6]}")
            tenant_b = TenantDB(id=tenant_b_id, name="Org B", slug=f"org-b-{uuid.uuid4().hex[:6]}")
            user_a = UserDB(id=user_a_id, tenant_id=tenant_a.id, email=f"user-a-{uuid.uuid4().hex[:6]}@example.com", role="admin")
            session.add_all([tenant_a, tenant_b, user_a])
            await session.commit()

    asyncio.run(_setup())

    # Generate token for Tenant A Admin
    token_a, _ = create_access_token(user_id=user_a_id, tenant_id=tenant_a_id, extra_claims={"role": "admin"})

    # 1. Query Tenant A's own summary -> 200 OK
    resp_self = client.get(
        "/api/v1/usage/summary",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_self.status_code == 200
    assert resp_self.json()["tenant_id"] == tenant_a_id

    # 2. Attempt to query Tenant B's summary -> 403 Forbidden
    resp_foreign = client.get(
        f"/api/v1/usage/summary?target_tenant_id={tenant_b_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_foreign.status_code == 403
    assert "Cannot query foreign tenant" in resp_foreign.json()["detail"]

    # 3. Attempt to query Tenant B's events -> 403 Forbidden
    resp_events = client.get(
        f"/api/v1/usage/events?target_tenant_id={tenant_b_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_events.status_code == 403

    # 4. Attempt to query Tenant B's quotas -> 403 Forbidden
    resp_quotas = client.get(
        f"/api/v1/usage/quotas?target_tenant_id={tenant_b_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_quotas.status_code == 403


def test_api_key_scope_enforcement_on_usage(usage_env):
    """API keys require 'usage:read' scope to query usage endpoints."""
    client, session_factory = usage_env
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    raw_key1, pfx1, _ = generate_api_key()
    hash1 = hash_api_key(raw_key1)

    raw_key2, pfx2, _ = generate_api_key()
    hash2 = hash_api_key(raw_key2)

    async def _setup():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Key Org", slug=f"key-org-{uuid.uuid4().hex[:6]}")
            user = UserDB(id=user_id, tenant_id=tenant_id, email=f"user-{uuid.uuid4().hex[:6]}@example.com")

            # Key 1: Only downloads:read
            key_without_scope = APIKeyDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=user_id,
                name="Key No Usage",
                key_prefix=pfx1,
                key_hash=hash1,
                permissions=["downloads:read"],
                is_active=True,
            )

            # Key 2: Has usage:read
            key_with_scope = APIKeyDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=user_id,
                name="Key With Usage",
                key_prefix=pfx2,
                key_hash=hash2,
                permissions=["downloads:read", "usage:read"],
                is_active=True,
            )
            session.add_all([tenant, user, key_without_scope, key_with_scope])
            await session.commit()

    asyncio.run(_setup())

    # Key 1 fails with 403
    r1 = client.get("/api/v1/usage/summary", headers={"X-API-Key": raw_key1})
    assert r1.status_code == 403
    assert "usage:read" in r1.json()["detail"]

    # Key 2 succeeds with 200
    r2 = client.get("/api/v1/usage/summary", headers={"X-API-Key": raw_key2})
    assert r2.status_code == 200
    assert r2.json()["tenant_id"] == tenant_id


def test_customer_cannot_ingest_usage_events(usage_env):
    """Customer JWTs and API keys cannot post arbitrary usage events (fail-closed trust boundary)."""
    client, session_factory = usage_env
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    raw_key, pfx, _ = generate_api_key()
    khash = hash_api_key(raw_key)

    async def _setup():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Security Org", slug=f"sec-{uuid.uuid4().hex[:6]}")
            user = UserDB(id=user_id, tenant_id=tenant_id, email=f"user-{uuid.uuid4().hex[:6]}@example.com", role="admin")
            sys_user = UserDB(id="sys_admin", tenant_id=tenant_id, email="sys@example.com", role="system_admin")
            api_key = APIKeyDB(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=user_id,
                name="Security Key",
                key_prefix=pfx,
                key_hash=khash,
                permissions=["downloads:read", "usage:read"],
                is_active=True,
            )
            session.add_all([tenant, user, sys_user, api_key])
            await session.commit()

    asyncio.run(_setup())

    cust_token, _ = create_access_token(user_id=user_id, tenant_id=tenant_id, extra_claims={"role": "admin"})
    sys_token, _ = create_access_token(user_id="sys_admin", tenant_id=tenant_id, extra_claims={"role": "system_admin"})

    payload = {
        "event_type": "download",
        "idempotency_key": "untrusted_inject_1",
        "bytes": 999999999,
        "quantity": 100.0,
    }

    # 1. Customer JWT fails with 403 Forbidden
    r_cust = client.post(
        "/api/v1/usage/events",
        headers={"Authorization": f"Bearer {cust_token}"},
        json=payload,
    )
    assert r_cust.status_code == 403
    assert "restricted to authorized internal system workers" in r_cust.json()["detail"]

    # 2. Commercial API key fails with 403 Forbidden
    r_key = client.post(
        "/api/v1/usage/events",
        headers={"X-API-Key": raw_key},
        json=payload,
    )
    assert r_key.status_code == 403
    assert "restricted to authorized internal system workers" in r_key.json()["detail"]

    # 3. System admin succeeds with 201 Created
    r_sys = client.post(
        "/api/v1/usage/events",
        headers={"Authorization": f"Bearer {sys_token}"},
        json=payload,
    )
    assert r_sys.status_code == 201
    assert r_sys.json()["idempotency_key"] == "untrusted_inject_1"


def test_conflicting_idempotency_payload_rejected(usage_env):
    """Replaying an idempotency key with conflicting values raises IdempotencyConflictError."""
    _, session_factory = usage_env
    tenant_id = str(uuid.uuid4())

    from api.services.usage_service import IdempotencyConflictError

    async def _test():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Idemp Conflict Org", slug=f"idemp-c-{uuid.uuid4().hex[:6]}")
            session.add(tenant)
            await session.commit()

            idemp_key = f"clip:conflict_test_{uuid.uuid4().hex[:8]}"

            # 1. First emission
            await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key=idemp_key,
                resource_id="clip_abc",
                quantity=1.0,
                unit="count",
                bytes=1000,
                duration_seconds=5.0,
            )
            await session.commit()

            # 2. Identical replay succeeds
            ev, is_new = await record_usage_event(
                session,
                tenant_id=tenant_id,
                event_type="clip",
                idempotency_key=idemp_key,
                resource_id="clip_abc",
                quantity=1.0,
                unit="count",
                bytes=1000,
                duration_seconds=5.0,
            )
            assert is_new is False

            # 3. Conflicting byte count raises IdempotencyConflictError
            with pytest.raises(IdempotencyConflictError, match="incoming parameters do not match"):
                await record_usage_event(
                    session,
                    tenant_id=tenant_id,
                    event_type="clip",
                    idempotency_key=idemp_key,
                    resource_id="clip_abc",
                    quantity=1.0,
                    unit="count",
                    bytes=999999,  # Conflict!
                    duration_seconds=5.0,
                )

            # 4. Conflicting event_type raises IdempotencyConflictError
            with pytest.raises(IdempotencyConflictError, match="event_type"):
                await record_usage_event(
                    session,
                    tenant_id=tenant_id,
                    event_type="download",  # Conflict!
                    idempotency_key=idemp_key,
                    resource_id="clip_abc",
                    quantity=1.0,
                    unit="count",
                    bytes=1000,
                    duration_seconds=5.0,
                )

    asyncio.run(_test())


def test_usage_reconciliation_audit_and_repair(usage_env):
    """Reconciliation identifies unmetered operations and safely repairs them."""
    _, session_factory = usage_env
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())[:8]

    from api.database.models import MediaArtifactDB
    from api.services.reconcile_usage import reconcile_usage_events

    async def _test():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Reconcile Org", slug=f"rec-{uuid.uuid4().hex[:6]}")
            user = UserDB(id=user_id, tenant_id=tenant_id, email=f"user-{uuid.uuid4().hex[:6]}@example.com")
            art = MediaArtifactDB(
                id=artifact_id,
                tenant_id=tenant_id,
                user_id=user_id,
                artifact_type="clip",
                storage_path="clips/sample.mp4",
                filename="sample.mp4",
                format="mp4",
                status="ready",
                file_size_bytes=45000,
                duration_seconds=3.5,
            )
            session.add_all([tenant, user, art])
            await session.commit()

            # 1. Audit mode (dry-run): identifies unmetered artifact without creating event
            rep1 = await reconcile_usage_events(session, tenant_id=tenant_id, dry_run=True)
            assert rep1.dry_run is True
            assert len(rep1.unmetered_artifacts) == 1
            assert rep1.unmetered_artifacts[0]["id"] == artifact_id
            assert rep1.repaired_count == 0

            # 2. Repair mode: creates event derived from MediaArtifactDB
            rep2 = await reconcile_usage_events(session, tenant_id=tenant_id, dry_run=False)
            assert rep2.dry_run is False
            assert rep2.repaired_count == 1
            assert f"clip:{artifact_id}" in rep2.repaired_keys

            # 3. Post-repair audit: 0 unmetered
            rep3 = await reconcile_usage_events(session, tenant_id=tenant_id, dry_run=True)
            assert len(rep3.unmetered_artifacts) == 0

    asyncio.run(_test())


def test_subtitle_retrieval_separated_from_ai_transcription(usage_env):
    """Subtitle downloads are categorized as subtitle_retrieval and do not count as AI whisper minutes."""
    _, session_factory = usage_env
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    from api.services.usage_service import record_subtitle_usage, record_transcription_usage

    async def _test():
        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Transcript Org", slug=f"tr-{uuid.uuid4().hex[:6]}")
            user = UserDB(id=user_id, tenant_id=tenant_id, email=f"user-{uuid.uuid4().hex[:6]}@example.com")
            session.add_all([tenant, user])
            await session.commit()

            # 1. Record subtitle retrieval
            await record_subtitle_usage(
                tenant_id=tenant_id,
                transcript_id="sub_123",
                word_count=500,
                bytes_count=2048,
                user_id=user_id,
            )

            # 2. Record AI transcription
            await record_transcription_usage(
                tenant_id=tenant_id,
                transcript_id="ai_456",
                duration_seconds=180.0,
                word_count=450,
                user_id=user_id,
            )

            summary = await get_tenant_usage_summary(session, tenant_id=tenant_id)
            totals = summary["totals"]
            assert totals["subtitles_count"] == 1
            assert totals["transcriptions_count"] == 1
            assert totals["transcription_minutes"] == 3.0  # 180s = 3.0 min

    asyncio.run(_test())
