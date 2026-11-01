"""Unit test suite for tenant-aware retention foundation and cleanup preview (STEP 02-E2A)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base
from api.database.models import DownloadJobDB, MediaArtifactDB, TenantDB, UserDB
from api.security.jwt import create_access_token
from api.security.media_storage import get_tenant_media_dir
from api.services.retention_service import (
    DEFAULT_RETENTION_DAYS,
    HISTORICAL_TEST_ANOMALIES,
    generate_retention_preview,
)


@pytest.fixture
def retention_test_env(tmp_path, monkeypatch):
    """Set up isolated test database and media storage for retention preview testing."""
    storage_root = tmp_path / "media_storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GID_DOWNLOAD__OUTPUT_DIRECTORY", str(storage_root))

    db_file = tmp_path / "retention_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("GID_API_DATABASE_URL", db_url)
    monkeypatch.setenv("GID_JWT_SECRET_KEY", "retention-foundation-secret-key-32-chars-long!")
    monkeypatch.setenv("GID_API_SKIP_SEED", "1")

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_a = "tenant-ret-001"
    tenant_b = "tenant-ret-002"

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            t1 = TenantDB(id=tenant_a, name="Retention Tenant A", slug="ret-a", tier="enterprise", is_active=True)
            t2 = TenantDB(id=tenant_b, name="Retention Tenant B", slug="ret-b", tier="basic", is_active=True)
            u1 = UserDB(id="user-ret-owner", tenant_id=tenant_a, email="owner@ret-a.local", role="owner", is_active=True)
            u2 = UserDB(id="user-ret-member", tenant_id=tenant_a, email="member@ret-a.local", role="member", is_active=True)
            session.add_all([t1, t2, u1, u2])
            await session.commit()

    asyncio.run(_setup())

    return {
        "engine": engine,
        "session_factory": session_factory,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "storage_root": storage_root,
    }


@pytest.mark.asyncio
async def test_default_retain_forever_policy(retention_test_env):
    """Under default retain-forever policy (-1), customer media is preserved and eligible_for_cleanup == 0."""
    session_factory = retention_test_env["session_factory"]
    tenant_a = retention_test_env["tenant_a"]
    storage_root = retention_test_env["storage_root"]

    # Create customer artifact created 60 days ago
    old_time = datetime.now(timezone.utc) - timedelta(days=60)
    async with session_factory() as s:
        art = MediaArtifactDB(
            id="cust-art-01",
            tenant_id=tenant_a,
            user_id="user-ret-owner",
            artifact_type="clip",
            storage_path="clips/customer_video_clip.mp4",
            filename="customer_video_clip.mp4",
            file_size_bytes=52428800,  # 50 MB
            duration_seconds=120.0,
            status="ready",
            created_at=old_time,
        )
        s.add(art)
        await s.commit()

    async with session_factory() as s:
        report = await generate_retention_preview(
            db=s,
            storage_root=storage_root,
            retention_days=DEFAULT_RETENTION_DAYS,  # -1
            filter_tenant_id=tenant_a,
        )

        assert report.policy == "retain_forever"
        assert report.effective_retention_days == -1
        assert report.total_scanned_artifacts == 1
        assert report.customer_media_retained_count == 1
        assert report.eligible_for_cleanup_count == 0
        assert report.estimated_reclaimable_bytes == 0

        item = report.items[0]
        assert item.classification == "customer_media_retained"
        assert item.action == "RETAIN"
        assert item.eligible_for_cleanup is False
        assert "Default retain-forever policy applies" in item.reason


@pytest.mark.asyncio
async def test_download_job_and_referenced_sources_protected(retention_test_env):
    """Media backed by download jobs or referenced as input sources is permanently protected from cleanup."""
    session_factory = retention_test_env["session_factory"]
    tenant_a = retention_test_env["tenant_a"]
    storage_root = retention_test_env["storage_root"]

    old_time = datetime.now(timezone.utc) - timedelta(days=90)

    async with session_factory() as s:
        # 1. Authoritative download job
        dj = DownloadJobDB(
            id="dj-source-01",
            url="https://example.com/source.mp4",
            provider="direct",
            media_type="video",
            status="completed",
            priority="normal",
            title="Source Video",
            file_path="downloads/source_video.mp4",
            tenant_id=tenant_a,
            user_id="user-ret-owner",
            created_at=old_time,
        )
        # 2. Source clip referenced by merge
        clip_parent = MediaArtifactDB(
            id="art-clip-parent",
            tenant_id=tenant_a,
            user_id="user-ret-owner",
            artifact_type="clip",
            source_download_id="dj-source-01",
            storage_path="clips/clip_parent.mp4",
            filename="clip_parent.mp4",
            file_size_bytes=1000000,
            status="ready",
            created_at=old_time,
        )
        # 3. Derived merge referencing clip_parent in input_sources
        merge_derived = MediaArtifactDB(
            id="art-merge-derived",
            tenant_id=tenant_a,
            user_id="user-ret-owner",
            artifact_type="merge",
            parent_artifact_id="art-clip-parent",
            input_sources=["clips/clip_parent.mp4"],
            storage_path="merged/merged_output.mp4",
            filename="merged_output.mp4",
            file_size_bytes=2000000,
            status="ready",
            created_at=old_time,
        )
        s.add_all([dj, clip_parent, merge_derived])
        await s.commit()

    # Even under a simulated explicit retention window (e.g. 30 days)
    async with session_factory() as s:
        report = await generate_retention_preview(
            db=s,
            storage_root=storage_root,
            retention_days=30,  # Explicit window
            filter_tenant_id=tenant_a,
        )

        # The parent clip is protected because it is backed by dj-source-01 AND referenced by merge
        items_by_id = {item.artifact_id: item for item in report.items}
        clip_item = items_by_id["art-clip-parent"]
        assert clip_item.is_protected is True
        assert clip_item.eligible_for_cleanup is False
        assert clip_item.action == "PRESERVE_PROTECTED"


@pytest.mark.asyncio
async def test_historical_test_anomalies_segregated(retention_test_env):
    """The 10 known pre-Fix-B test artifacts are classified separately without modifying records."""
    session_factory = retention_test_env["session_factory"]
    tenant_a = retention_test_env["tenant_a"]
    storage_root = retention_test_env["storage_root"]

    # Insert known historical anomaly IDs
    anomaly_ids = ["f76f7f6cc0c0", "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"]
    async with session_factory() as s:
        anomalies = [
            MediaArtifactDB(
                id=art_id,
                tenant_id=tenant_a,
                user_id="user-ret-owner",
                artifact_type="clip" if "clip" in art_id or art_id == "f76f7f6cc0c0" else "merge",
                storage_path=f"test/{art_id}.mp4",
                filename=f"{art_id}.mp4",
                status="processing" if art_id == "f76f7f6cc0c0" else "ready",
            )
            for art_id in anomaly_ids
        ]
        s.add_all(anomalies)
        await s.commit()

    async with session_factory() as s:
        report = await generate_retention_preview(
            db=s,
            storage_root=storage_root,
            retention_days=DEFAULT_RETENTION_DAYS,
            filter_tenant_id=tenant_a,
        )

        assert report.historical_anomalies_count == 3
        for item in report.items:
            assert item.classification == "historical_test_anomaly"
            assert item.action == "PRESERVE_UNMODIFIED"
            assert item.eligible_for_cleanup is False

        # Invariance check: verify DB status was NOT mutated
        res = await s.execute(select(MediaArtifactDB).where(MediaArtifactDB.id == "f76f7f6cc0c0"))
        stale_record = res.scalar_one()
        assert stale_record.status == "processing"


@pytest.mark.asyncio
async def test_strictly_read_only_invariance(retention_test_env):
    """Retention preview guarantees zero mutations: no rows modified, no files deleted."""
    session_factory = retention_test_env["session_factory"]
    tenant_a = retention_test_env["tenant_a"]
    storage_root = retention_test_env["storage_root"]

    clips_dir = get_tenant_media_dir(tenant_a, "clips")
    test_file = clips_dir / "preview_file.mp4"
    test_file.write_bytes(b"TEST MEDIA BYTES")

    async with session_factory() as s:
        art = MediaArtifactDB(
            id="art-invariance-test",
            tenant_id=tenant_a,
            user_id="user-ret-owner",
            artifact_type="clip",
            storage_path=f"clips/{test_file.name}",
            filename=test_file.name,
            file_size_bytes=len(b"TEST MEDIA BYTES"),
            status="ready",
            created_at=datetime.now(timezone.utc) - timedelta(days=365),
        )
        s.add(art)
        await s.commit()

    # Run preview with aggressive 1-day retention
    async with session_factory() as s:
        report = await generate_retention_preview(
            db=s,
            storage_root=storage_root,
            retention_days=1,
            filter_tenant_id=tenant_a,
        )
        assert report.eligible_for_cleanup_count == 1

        # Verify physical file STILL EXISTS on disk!
        assert test_file.exists()

        # Verify database record STILL EXISTS and status is STILL ready!
        chk = (await s.execute(select(MediaArtifactDB).where(MediaArtifactDB.id == "art-invariance-test"))).scalar_one()
        assert chk.status == "ready"


@pytest.mark.asyncio
async def test_in_progress_artifacts_excluded_from_retention(retention_test_env):
    """Artifacts in 'pending' or 'processing' states are strictly excluded from retention cleanup."""
    session_factory = retention_test_env["session_factory"]
    tenant_a = retention_test_env["tenant_a"]
    storage_root = retention_test_env["storage_root"]

    old_time = datetime.now(timezone.utc) - timedelta(days=90)
    async with session_factory() as s:
        p1 = MediaArtifactDB(
            id="art-pending-01",
            tenant_id=tenant_a,
            user_id="user-ret-owner",
            artifact_type="clip",
            storage_path="clips/pending_clip.mp4",
            filename="pending_clip.mp4",
            file_size_bytes=1024,
            status="pending",
            created_at=old_time,
        )
        p2 = MediaArtifactDB(
            id="art-processing-02",
            tenant_id=tenant_a,
            user_id="user-ret-owner",
            artifact_type="merge",
            storage_path="merged/processing_merge.mp4",
            filename="processing_merge.mp4",
            file_size_bytes=2048,
            status="processing",
            created_at=old_time,
        )
        s.add_all([p1, p2])
        await s.commit()

    async with session_factory() as s:
        report = await generate_retention_preview(
            db=s,
            storage_root=storage_root,
            retention_days=7,  # Aggressive 7-day retention
            filter_tenant_id=tenant_a,
        )

        # Both pending and processing must be excluded from cleanup
        assert report.eligible_for_cleanup_count == 0
        in_progress_items = [it for it in report.items if it.artifact_id in ("art-pending-01", "art-processing-02")]
        assert len(in_progress_items) == 2
        for it in in_progress_items:
            assert it.classification == "in_progress_excluded"
            assert it.action == "EXCLUDE_IN_PROGRESS"
            assert it.is_protected is True
            assert it.eligible_for_cleanup is False


@pytest.mark.asyncio
async def test_invalid_storage_root_safe_error(retention_test_env):
    """Specifying an invalid or non-existent storage root produces a clean FileNotFoundError."""
    session_factory = retention_test_env["session_factory"]
    tenant_a = retention_test_env["tenant_a"]

    invalid_root = Path("/nonexistent/storage/path/mediaforge_test_12345")
    async with session_factory() as s:
        with pytest.raises(FileNotFoundError) as exc_info:
            await generate_retention_preview(
                db=s,
                storage_root=invalid_root,
                filter_tenant_id=tenant_a,
            )
        assert "does not exist or is not a directory" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tenant_isolation_and_cross_tenant_filtration(retention_test_env):
    """Filtering by Tenant A strictly prevents Tenant B artifacts or summaries from appearing."""
    session_factory = retention_test_env["session_factory"]
    tenant_a = retention_test_env["tenant_a"]
    tenant_b = retention_test_env["tenant_b"]
    storage_root = retention_test_env["storage_root"]

    async with session_factory() as s:
        b_art = MediaArtifactDB(
            id="art-tenant-b-private",
            tenant_id=tenant_b,
            user_id="user-b-owner",
            artifact_type="clip",
            storage_path="clips/secret_b.mp4",
            filename="secret_b.mp4",
            file_size_bytes=100000,
            status="ready",
            created_at=datetime.now(timezone.utc),
        )
        s.add(b_art)
        await s.commit()

    async with session_factory() as s:
        report = await generate_retention_preview(
            db=s,
            storage_root=storage_root,
            filter_tenant_id=tenant_a,
        )

        assert report.filter_tenant_id == tenant_a
        assert tenant_b not in report.tenant_summaries
        for item in report.items:
            assert item.tenant_id == tenant_a
            assert item.artifact_id != "art-tenant-b-private"


def test_retention_api_security_and_route_authorization(retention_test_env):
    """Audit GET /api/v1/artifacts/retention/preview endpoint security boundaries."""
    from fastapi.testclient import TestClient
    from api.main import app
    from api.database.connection import get_session

    session_factory = retention_test_env["session_factory"]

    async def _mock_get_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _mock_get_session
    try:
        client = TestClient(app)
        tenant_a = retention_test_env["tenant_a"]
        tenant_b = retention_test_env["tenant_b"]

        # 1. Unauthenticated request -> 401 Unauthorized
        res = client.get("/api/v1/artifacts/retention/preview")
        assert res.status_code == 401

        # 2. Authenticated as non-admin member -> 403 Forbidden
        member_token, _ = create_access_token(
            user_id="user-ret-member",
            tenant_id=tenant_a,
            extra_claims={"role": "member", "tier": "basic"},
        )
        res = client.get(
            "/api/v1/artifacts/retention/preview",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert res.status_code == 403
        assert "administrator or owner privileges" in res.json()["detail"]

        # 3. Authenticated via API key with missing 'clips:read' scope -> 403 Forbidden
        from api.services.api_key_service import APIKeyService
        async def _create_test_key():
            async with session_factory() as s:
                _, raw = await APIKeyService.create_key(
                    session=s,
                    tenant_id=tenant_a,
                    user_id="user-ret-owner",
                    name="Test Key Missing Scope",
                    scopes=["downloads:read"],
                )
                return raw

        raw_bad_key = asyncio.run(_create_test_key())
        res = client.get(
            "/api/v1/artifacts/retention/preview",
            headers={"X-API-Key": raw_bad_key},
        )
        assert res.status_code == 403
        assert "Insufficient API key permissions" in res.json()["detail"]

        # 4. Authenticated as owner of Tenant A trying to access Tenant B -> 403 Forbidden
        owner_token_a, _ = create_access_token(
            user_id="user-ret-owner",
            tenant_id=tenant_a,
            extra_claims={"role": "owner", "tier": "platinum"},
        )
        res = client.get(
            f"/api/v1/artifacts/retention/preview?target_tenant_id={tenant_b}",
            headers={"Authorization": f"Bearer {owner_token_a}"},
        )
        assert res.status_code == 403
        assert "not authorized for target tenant resources" in res.json()["detail"]

        # 5. Authenticated as owner of Tenant A querying own preview -> 200 OK
        res = client.get(
            "/api/v1/artifacts/retention/preview",
            headers={"Authorization": f"Bearer {owner_token_a}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["policy"] == "retain_forever"
        assert data["storage_root"] == "downloads"  # Masked internal path
        assert data["filter_tenant_id"] == tenant_a
    finally:
        app.dependency_overrides.clear()
