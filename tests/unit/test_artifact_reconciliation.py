"""Unit test suite for safe, idempotent artifact reconciliation (STEP 02-E1-Fix-A)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base
from api.database.models import DownloadJobDB, MediaArtifactDB, TenantDB, UserDB
from api.security.media_storage import get_tenant_media_dir, get_tenant_storage_root
from api.services.reconcile_artifacts import run_reconciliation


@pytest.fixture
def reconciliation_env(tmp_path, monkeypatch):
    """Set up isolated test database and media storage for reconciliation testing."""
    storage_root = tmp_path / "media_storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GID_DOWNLOAD__OUTPUT_DIRECTORY", str(storage_root))

    db_file = tmp_path / "reconciliation_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("GID_API_DATABASE_URL", db_url)
    monkeypatch.setenv("GID_JWT_SECRET_KEY", "reconciliation-test-secret-32-bytes-long!!")
    monkeypatch.setenv("GID_API_SKIP_SEED", "1")

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_id = "tenant-recon-001"

    async def _setup_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            tenant = TenantDB(id=tenant_id, name="Recon Tenant", slug="recon", tier="platinum", is_active=True)
            user = UserDB(id="user-recon-1", tenant_id=tenant_id, email="recon@example.local", role="owner", is_active=True)
            session.add_all([tenant, user])
            await session.commit()

    asyncio.run(_setup_db())

    return {
        "engine": engine,
        "session_factory": session_factory,
        "tenant_id": tenant_id,
        "storage_root": storage_root,
    }


@pytest.mark.asyncio
async def test_reconciliation_clean_state(reconciliation_env):
    """Clean state with matching ready records and files produces zero anomalies."""
    session_factory = reconciliation_env["session_factory"]
    tenant_id = reconciliation_env["tenant_id"]
    storage_root = reconciliation_env["storage_root"]

    clips_dir = get_tenant_media_dir(tenant_id, "clips")
    clip_file = clips_dir / "valid_clip.mp4"
    clip_file.write_bytes(b"VALID CLIP PAYLOAD")

    async with session_factory() as s:
        art = MediaArtifactDB(
            id="art-valid-01",
            tenant_id=tenant_id,
            user_id="user-recon-1",
            artifact_type="clip",
            storage_path=f"clips/{clip_file.name}",
            filename=clip_file.name,
            file_size_bytes=len(b"VALID CLIP PAYLOAD"),
            status="ready",
        )
        s.add(art)
        await s.commit()

    async with session_factory() as s:
        report = await run_reconciliation(s, storage_root=storage_root, stale_timeout_seconds=1800)
        assert report.scanned_artifacts_count == 1
        assert report.stale_processing_count == 0
        assert report.missing_ready_count == 0
        assert report.orphaned_files_count == 0
        assert report.inconsistent_deletions_count == 0
        assert not report.has_anomalies


@pytest.mark.asyncio
async def test_reconciliation_detects_all_anomaly_types(reconciliation_env):
    """Detects stale processing, missing ready files, inconsistent deletions, and orphaned files."""
    session_factory = reconciliation_env["session_factory"]
    tenant_id = reconciliation_env["tenant_id"]
    storage_root = reconciliation_env["storage_root"]

    clips_dir = get_tenant_media_dir(tenant_id, "clips")
    temp_dir = get_tenant_media_dir(tenant_id, "temp")

    # 1. Stale processing artifact (created 2 hours ago)
    past_time = datetime.utcnow() - timedelta(hours=2)

    # 2. Inconsistent deletion (DB says deleted, but file exists)
    deleted_file = clips_dir / "deleted_still_here.mp4"
    deleted_file.write_bytes(b"SHOULD HAVE BEEN DELETED")

    # 3. Orphaned temporary file on disk
    orphan_tmp = temp_dir / "_temp_forgotten.mp4"
    orphan_tmp.write_bytes(b"TMP WORKSPACE CRUFT")

    # 4. Unindexed file on disk
    unindexed_clip = clips_dir / "unindexed_legacy.mp4"
    unindexed_clip.write_bytes(b"LEGACY NON-DB FILE")

    async with session_factory() as s:
        # Stale processing
        art_stale = MediaArtifactDB(
            id="art-stale-01",
            tenant_id=tenant_id,
            user_id="user-recon-1",
            artifact_type="clip",
            storage_path="clips/stale_clip.mp4",
            filename="stale_clip.mp4",
            status="processing",
            created_at=past_time,
        )
        # Missing ready file (no file created on disk)
        art_missing = MediaArtifactDB(
            id="art-missing-02",
            tenant_id=tenant_id,
            user_id="user-recon-1",
            artifact_type="clip",
            storage_path="clips/missing_on_disk.mp4",
            filename="missing_on_disk.mp4",
            status="ready",
        )
        # Inconsistent deletion
        art_deleted = MediaArtifactDB(
            id="art-del-03",
            tenant_id=tenant_id,
            user_id="user-recon-1",
            artifact_type="clip",
            storage_path=f"clips/{deleted_file.name}",
            filename=deleted_file.name,
            status="deleted",
        )
        s.add_all([art_stale, art_missing, art_deleted])
        await s.commit()

    async with session_factory() as s:
        report = await run_reconciliation(s, storage_root=storage_root, stale_timeout_seconds=1800)

        assert report.has_anomalies
        assert report.stale_processing_count == 1
        assert report.missing_ready_count == 1
        assert report.inconsistent_deletions_count == 1
        assert report.orphaned_files_count == 2  # _temp_forgotten.mp4 and unindexed_legacy.mp4

        categories = [item.category for item in report.items]
        assert "stale_processing" in categories
        assert "missing_ready_file" in categories
        assert "inconsistent_deletion" in categories
        assert "orphaned_temporary_file" in categories
        assert "orphaned_unindexed_file" in categories

        # DRY RUN SAFETY: verify no files were deleted and no DB rows changed
        assert deleted_file.exists()
        assert orphan_tmp.exists()
        assert unindexed_clip.exists()

        check_stale = (await s.execute(select(MediaArtifactDB).where(MediaArtifactDB.id == "art-stale-01"))).scalar_one()
        assert check_stale.status == "processing"  # Untouched by dry-run


@pytest.mark.asyncio
async def test_reconciliation_detects_storage_mount_errors(reconciliation_env, tmp_path):
    """Detects missing or non-directory storage root as a storage mount error."""
    session_factory = reconciliation_env["session_factory"]

    # 1. Non-existent storage directory
    non_existent = tmp_path / "does_not_exist_volume"
    async with session_factory() as s:
        report = await run_reconciliation(s, storage_root=non_existent)
        assert report.has_anomalies
        assert report.storage_mount_errors_count == 1
        assert any(item.category == "storage_mount_missing" for item in report.items)

    # 2. File instead of directory
    file_as_dir = tmp_path / "regular_file.txt"
    file_as_dir.write_text("not a directory")
    async with session_factory() as s:
        report = await run_reconciliation(s, storage_root=file_as_dir)
        assert report.has_anomalies
        assert report.storage_mount_errors_count == 1
        assert any(item.category == "storage_mount_invalid" for item in report.items)
