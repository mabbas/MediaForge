"""Authoritative Usage Reconciliation Service (STEP 02-E2B-Fix-A).

Identifies completed media operations (clips, merges, downloads) missing their
authoritative usage events and safely repairs them using trusted source records.

Operates in:
- Read-only audit mode (dry_run=True, default): identifies unmetered operations without modifying data.
- Controlled idempotent repair mode (dry_run=False): inserts missing usage events derived
  strictly from persisted MediaArtifactDB and DownloadJobDB source records.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import close_database, get_session_factory, init_database
from api.database.models import DownloadJobDB, MediaArtifactDB, UsageEventDB
from api.services.usage_service import record_usage_event

logger = logging.getLogger(__name__)


@dataclass
class UsageReconciliationReport:
    """Report detailing unmetered operations and reconciliation actions."""

    dry_run: bool = True
    scanned_artifacts: int = 0
    scanned_downloads: int = 0
    unmetered_artifacts: list[dict[str, Any]] = field(default_factory=list)
    unmetered_downloads: list[dict[str, Any]] = field(default_factory=list)
    repaired_count: int = 0
    repaired_keys: list[str] = field(default_factory=list)

    @property
    def total_unmetered(self) -> int:
        return len(self.unmetered_artifacts) + len(self.unmetered_downloads)


async def reconcile_usage_events(
    session: AsyncSession,
    *,
    tenant_id: str | None = None,
    dry_run: bool = True,
    target_artifact_ids: Sequence[str] | None = None,
    target_job_ids: Sequence[str] | None = None,
) -> UsageReconciliationReport:
    """Identify and safely reconcile unmetered media operations from trusted database records."""
    report = UsageReconciliationReport(dry_run=dry_run)

    # 1. Audit MediaArtifactDB (clips & merges)
    art_stmt = select(MediaArtifactDB).where(
        MediaArtifactDB.status == "ready",
        MediaArtifactDB.artifact_type.in_(["clip", "merge"]),
    )
    if tenant_id:
        art_stmt = art_stmt.where(MediaArtifactDB.tenant_id == tenant_id)
    if target_artifact_ids:
        art_stmt = art_stmt.where(MediaArtifactDB.id.in_(target_artifact_ids))

    art_res = await session.execute(art_stmt)
    artifacts = art_res.scalars().all()
    report.scanned_artifacts = len(artifacts)

    for art in artifacts:
        expected_key = f"{art.artifact_type}:{art.id}"
        chk_stmt = select(UsageEventDB.id).where(
            UsageEventDB.tenant_id == art.tenant_id,
            UsageEventDB.idempotency_key == expected_key,
        )
        has_event = (await session.execute(chk_stmt)).scalar_one_or_none()
        if not has_event:
            record_info = {
                "id": art.id,
                "tenant_id": art.tenant_id,
                "user_id": art.user_id,
                "artifact_type": art.artifact_type,
                "file_size_bytes": art.file_size_bytes or 0,
                "duration_seconds": art.duration_seconds or 0.0,
                "expected_key": expected_key,
            }
            report.unmetered_artifacts.append(record_info)

            if not dry_run:
                try:
                    await record_usage_event(
                        session,
                        tenant_id=art.tenant_id,
                        event_type=art.artifact_type,
                        idempotency_key=expected_key,
                        resource_id=art.id,
                        quantity=1.0,
                        unit="count",
                        bytes=art.file_size_bytes or 0,
                        duration_seconds=art.duration_seconds or 0.0,
                        user_id=art.user_id,
                        auth_method="reconciliation",
                        metadata_json={
                            "reconciled": True,
                            "source": "reconcile_usage_service",
                            "storage_path": art.storage_path,
                        },
                    )
                    report.repaired_count += 1
                    report.repaired_keys.append(expected_key)
                except Exception as exc:
                    logger.warning("Could not reconcile usage for artifact %s: %s", art.id, exc)

    # 2. Audit DownloadJobDB (completed downloads)
    job_stmt = select(DownloadJobDB).where(DownloadJobDB.status == "completed")
    if tenant_id:
        job_stmt = job_stmt.where(DownloadJobDB.tenant_id == tenant_id)
    if target_job_ids:
        job_stmt = job_stmt.where(DownloadJobDB.id.in_(target_job_ids))

    job_res = await session.execute(job_stmt)
    jobs = job_res.scalars().all()
    report.scanned_downloads = len(jobs)

    for job in jobs:
        expected_key = f"download:{job.id}"
        chk_stmt = select(UsageEventDB.id).where(
            UsageEventDB.tenant_id == job.tenant_id,
            UsageEventDB.idempotency_key == expected_key,
        )
        has_event = (await session.execute(chk_stmt)).scalar_one_or_none()
        if not has_event:
            job_info = {
                "id": job.id,
                "tenant_id": job.tenant_id,
                "user_id": job.user_id,
                "file_size_bytes": job.file_size_bytes or 0,
                "duration_seconds": float(job.duration_seconds or 0.0),
                "expected_key": expected_key,
            }
            report.unmetered_downloads.append(job_info)

            if not dry_run:
                try:
                    await record_usage_event(
                        session,
                        tenant_id=job.tenant_id,
                        event_type="download",
                        idempotency_key=expected_key,
                        resource_id=job.id,
                        quantity=1.0,
                        unit="count",
                        bytes=job.file_size_bytes or 0,
                        duration_seconds=float(job.duration_seconds or 0.0),
                        user_id=job.user_id,
                        auth_method="reconciliation",
                        metadata_json={
                            "reconciled": True,
                            "source": "reconcile_usage_service",
                            "url": job.url,
                        },
                    )
                    report.repaired_count += 1
                    report.repaired_keys.append(expected_key)
                except Exception as exc:
                    logger.warning("Could not reconcile usage for download %s: %s", job.id, exc)

    if not dry_run and report.repaired_count > 0:
        await session.commit()

    return report


async def main() -> None:
    """CLI Entrypoint for running usage reconciliation."""
    parser = argparse.ArgumentParser(description="MediaForge Authoritative Usage Reconciliation")
    parser.add_argument("--tenant-id", type=str, default=None, help="Filter by specific tenant ID")
    parser.add_argument("--repair", action="store_true", help="Execute repair (default is dry-run audit only)")
    parser.add_argument("--database-url", type=str, default=None, help="Override database URL")
    args = parser.parse_args()

    import os
    db_url = args.database_url or os.environ.get("GID_API_DATABASE_URL")
    if not db_url:
        from api.config import get_api_settings
        db_url = get_api_settings().database_url

    await init_database(db_url, validate_revision=False)
    factory = get_session_factory()
    if not factory:
        print("ERROR: Could not initialize database factory.")
        return

    async with factory() as session:
        rep = await reconcile_usage_events(
            session,
            tenant_id=args.tenant_id,
            dry_run=not args.repair,
        )
        print("=== MediaForge Usage Reconciliation Report ===")
        print(f"Mode: {'DRY RUN (Read-Only Audit)' if rep.dry_run else 'REPAIR APPLIED'}")
        print(f"Scanned Artifacts: {rep.scanned_artifacts}")
        print(f"Scanned Downloads: {rep.scanned_downloads}")
        print(f"Unmetered Artifacts Found: {len(rep.unmetered_artifacts)}")
        for a in rep.unmetered_artifacts:
            print(f"  * [{a['artifact_type'].upper()}] id={a['id']} tenant={a['tenant_id']} size={a['file_size_bytes']}B dur={a['duration_seconds']}s")
        print(f"Unmetered Downloads Found: {len(rep.unmetered_downloads)}")
        for d in rep.unmetered_downloads:
            print(f"  * [DOWNLOAD] id={d['id']} tenant={d['tenant_id']} size={d['file_size_bytes']}B")
        if not rep.dry_run:
            print(f"Repaired Events Inserted: {rep.repaired_count}")
        print("==============================================")

    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
