"""MediaForge Artifact Reconciliation Utility (STEP 02-E1-Fix-B).

Performs safe, idempotent, dry-run reconciliation between the PostgreSQL database
(`gid.media_artifacts`, `gid.download_jobs`) and the filesystem media storage.

Audits and reports:
1. Storage mount misconfiguration and directory accessibility
2. Stale processing artifacts (stuck in 'pending' or 'processing' past timeout)
3. Missing ready files ('ready' in DB but missing from disk)
4. Orphaned temporary work files (residual files left in temp or named _temp_*)
5. Orphaned unindexed media files (valid files on disk with no DB record)
6. Inconsistent deletions ('deleted' in DB but file still present on disk)

SAFE & IDEMPOTENT: This command is strictly read-only by default and never deletes user media.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Add project root to sys.path if running as standalone script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from api.config import get_api_settings
from api.database.models import DownloadJobDB, MediaArtifactDB
from api.security.media_storage import (
    get_media_storage_root,
    get_tenant_storage_root,
)

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationItem:
    """Represents a single detected inconsistency."""
    category: str
    artifact_id: Optional[str]
    tenant_id: Optional[str]
    storage_path: Optional[str]
    status: Optional[str]
    details: str
    recommended_action: str


@dataclass
class ReconciliationReport:
    """Summary and details of the reconciliation run."""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    storage_root: str = ""
    scanned_artifacts_count: int = 0
    scanned_files_count: int = 0
    stale_processing_count: int = 0
    missing_ready_count: int = 0
    orphaned_temp_count: int = 0
    orphaned_unindexed_count: int = 0
    orphaned_files_count: int = 0  # Total orphaned: temp + unindexed (backward compatibility)
    inconsistent_deletions_count: int = 0
    storage_mount_errors_count: int = 0
    inaccessible_paths_count: int = 0
    items: list[ReconciliationItem] = field(default_factory=list)

    @property
    def has_anomalies(self) -> bool:
        return (
            self.stale_processing_count > 0
            or self.missing_ready_count > 0
            or self.orphaned_files_count > 0
            or self.inconsistent_deletions_count > 0
            or self.storage_mount_errors_count > 0
            or self.inaccessible_paths_count > 0
        )


async def run_reconciliation(
    db: AsyncSession,
    storage_root: Path | None = None,
    stale_timeout_seconds: int = 1800,  # 30 minutes default
    filter_tenant_id: str | None = None,
) -> ReconciliationReport:
    """Perform a dry-run audit comparing DB records and filesystem state."""
    base_root = (storage_root or get_media_storage_root()).resolve()
    report = ReconciliationReport(storage_root=str(base_root))

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=stale_timeout_seconds)

    # 0. Check Storage Mount Integrity
    if not base_root.exists():
        report.storage_mount_errors_count += 1
        report.items.append(
            ReconciliationItem(
                category="storage_mount_missing",
                artifact_id=None,
                tenant_id=None,
                storage_path=str(base_root),
                status=None,
                details=f"Configured media storage root does not exist on filesystem: '{base_root}'",
                recommended_action="Verify Docker volume mounts and GID_DOWNLOAD__OUTPUT_DIRECTORY configuration",
            )
        )
        return report

    if not base_root.is_dir():
        report.storage_mount_errors_count += 1
        report.items.append(
            ReconciliationItem(
                category="storage_mount_invalid",
                artifact_id=None,
                tenant_id=None,
                storage_path=str(base_root),
                status=None,
                details=f"Configured media storage root is not a directory: '{base_root}'",
                recommended_action="Ensure storage root path points to a valid directory or volume mount",
            )
        )
        return report

    if not os.access(base_root, os.R_OK):
        report.storage_mount_errors_count += 1
        report.items.append(
            ReconciliationItem(
                category="storage_mount_permission_denied",
                artifact_id=None,
                tenant_id=None,
                storage_path=str(base_root),
                status=None,
                details=f"Storage root is not readable by process user: '{base_root}'",
                recommended_action="Verify ownership and permissions on media volume (UID 1000 mediaforge)",
            )
        )
        return report

    # 1. Inspect all MediaArtifactDB records
    query = select(MediaArtifactDB)
    if filter_tenant_id:
        query = query.where(MediaArtifactDB.tenant_id == filter_tenant_id)

    res = await db.execute(query)
    artifacts = list(res.scalars().all())
    report.scanned_artifacts_count = len(artifacts)

    known_active_paths: set[tuple[str, str]] = set()
    known_deleted_paths: set[tuple[str, str]] = set()

    for art in artifacts:
        tenant_root = (base_root / art.tenant_id).resolve()
        file_path = (tenant_root / art.storage_path).resolve()
        rel_str = str(art.storage_path).replace("\\", "/")

        # Distinguish inaccessible directory from absent file
        file_exists = False
        is_inaccessible = False
        try:
            if tenant_root.exists():
                file_exists = file_path.exists()
        except PermissionError as exc:
            is_inaccessible = True
            report.inaccessible_paths_count += 1
            report.items.append(
                ReconciliationItem(
                    category="inaccessible_directory",
                    artifact_id=art.id,
                    tenant_id=art.tenant_id,
                    storage_path=art.storage_path,
                    status=art.status,
                    details=f"Permission denied accessing storage directory: {exc}",
                    recommended_action="Verify directory permissions for tenant folder",
                )
            )
        except OSError as exc:
            is_inaccessible = True
            report.inaccessible_paths_count += 1
            report.items.append(
                ReconciliationItem(
                    category="filesystem_error",
                    artifact_id=art.id,
                    tenant_id=art.tenant_id,
                    storage_path=art.storage_path,
                    status=art.status,
                    details=f"Filesystem error checking file: {exc}",
                    recommended_action="Investigate storage device health and mount stability",
                )
            )

        if is_inaccessible:
            continue

        if art.status != "deleted":
            known_active_paths.add((art.tenant_id, rel_str))
        else:
            known_deleted_paths.add((art.tenant_id, rel_str))

        # Check 1: Stale processing
        if art.status in ("pending", "processing"):
            if art.created_at:
                art_time = art.created_at.replace(tzinfo=timezone.utc) if art.created_at.tzinfo is None else art.created_at
            else:
                art_time = now

            if art_time < stale_cutoff:
                report.stale_processing_count += 1
                age_min = int((now - art_time).total_seconds() / 60)
                report.items.append(
                    ReconciliationItem(
                        category="stale_processing",
                        artifact_id=art.id,
                        tenant_id=art.tenant_id,
                        storage_path=art.storage_path,
                        status=art.status,
                        details=f"Artifact stuck in '{art.status}' state for {age_min} minutes (created at {art.created_at})",
                        recommended_action="Mark status as 'failed' with error: 'Processing timed out or interrupted'",
                    )
                )

        # Check 2: Missing ready files
        elif art.status == "ready":
            if not file_exists or not file_path.is_file():
                report.missing_ready_count += 1
                report.items.append(
                    ReconciliationItem(
                        category="missing_ready_file",
                        artifact_id=art.id,
                        tenant_id=art.tenant_id,
                        storage_path=art.storage_path,
                        status=art.status,
                        details=f"File expected at '{art.storage_path}' ({art.file_size_bytes} bytes) does not exist on disk",
                        recommended_action="Flag database record as broken or missing storage file",
                    )
                )

        # Check 3: Inconsistent deletions (status='deleted' but file on disk)
        elif art.status == "deleted":
            if file_exists and file_path.is_file():
                report.inconsistent_deletions_count += 1
                file_size = file_path.stat().st_size
                report.items.append(
                    ReconciliationItem(
                        category="inconsistent_deletion",
                        artifact_id=art.id,
                        tenant_id=art.tenant_id,
                        storage_path=art.storage_path,
                        status=art.status,
                        details=f"Artifact marked 'deleted' in DB but physical file still exists ({file_size} bytes)",
                        recommended_action="Safely unlink orphaned file from disk storage",
                    )
                )

    # Also register known download_jobs files
    dj_query = select(DownloadJobDB)
    if filter_tenant_id:
        dj_query = dj_query.where(DownloadJobDB.tenant_id == filter_tenant_id)
    dj_res = await db.execute(dj_query)
    for dj in dj_res.scalars().all():
        if dj.tenant_id and dj.file_path:
            norm_dj = str(dj.file_path).replace("\\", "/")
            known_active_paths.add((dj.tenant_id, norm_dj))

    # 2. Inspect filesystem for orphaned files
    try:
        tenant_dirs = [d for d in base_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except PermissionError as exc:
        report.storage_mount_errors_count += 1
        report.items.append(
            ReconciliationItem(
                category="storage_mount_permission_denied",
                artifact_id=None,
                tenant_id=None,
                storage_path=str(base_root),
                status=None,
                details=f"Permission denied enumerating tenant directories: {exc}",
                recommended_action="Check directory permissions on storage root",
            )
        )
        return report

    if filter_tenant_id:
        tenant_dirs = [d for d in tenant_dirs if d.name == filter_tenant_id]

    for t_dir in tenant_dirs:
        t_id = t_dir.name
        try:
            for f in t_dir.rglob("*"):
                if not f.is_file():
                    continue

                report.scanned_files_count += 1
                rel_to_tenant = f.relative_to(t_dir).as_posix()

                # Orphaned temporary file check
                is_temp_file = (
                    f.name.startswith("_temp_")
                    or f.parent.name == "temp"
                    or "temp/" in rel_to_tenant
                    or f.name.endswith(".tmp")
                )

                if is_temp_file:
                    report.orphaned_temp_count += 1
                    report.orphaned_files_count += 1
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                    age_sec = int((now - mtime).total_seconds())
                    report.items.append(
                        ReconciliationItem(
                            category="orphaned_temporary_file",
                            artifact_id=None,
                            tenant_id=t_id,
                            storage_path=rel_to_tenant,
                            status=None,
                            details=f"Residual temporary file on disk (size: {f.stat().st_size} bytes, age: {age_sec}s)",
                            recommended_action="Purge uncleaned temporary work file",
                        )
                    )
                elif (t_id, rel_to_tenant) in known_deleted_paths:
                    # Already tracked under inconsistent_deletion
                    continue
                elif (t_id, rel_to_tenant) not in known_active_paths:
                    # Legacy or unindexed media file
                    report.orphaned_unindexed_count += 1
                    report.orphaned_files_count += 1
                    report.items.append(
                        ReconciliationItem(
                            category="orphaned_unindexed_file",
                            artifact_id=None,
                            tenant_id=t_id,
                            storage_path=rel_to_tenant,
                            status=None,
                            details=f"Unindexed media file without authoritative DB record (size: {f.stat().st_size} bytes)",
                            recommended_action="Preserve on disk; ingest into MediaArtifactDB or require operator reconciliation",
                        )
                    )
        except (PermissionError, OSError) as exc:
            report.inaccessible_paths_count += 1
            report.items.append(
                ReconciliationItem(
                    category="inaccessible_directory",
                    artifact_id=None,
                    tenant_id=t_id,
                    storage_path=str(t_dir),
                    status=None,
                    details=f"Error scanning tenant directory: {exc}",
                    recommended_action="Verify tenant folder permissions",
                )
            )

    return report


def print_report(report: ReconciliationReport, as_json: bool = False) -> None:
    """Print reconciliation report in human-readable or JSON format."""
    if as_json:
        print(json.dumps(asdict(report), indent=2))
        return

    print("=" * 75)
    print(" MediaForge Media Artifact Reconciliation Audit (Dry-Run)")
    print("=" * 75)
    print(f"Timestamp:              {report.timestamp}")
    print(f"Storage Base Root:      {report.storage_root}")
    print(f"Scanned DB Artifacts:   {report.scanned_artifacts_count}")
    print(f"Scanned Files on Disk:  {report.scanned_files_count}")
    print("-" * 75)
    print(f"Stale Processing Items: {report.stale_processing_count}")
    print(f"Missing Ready Files:    {report.missing_ready_count}")
    print(f"Inconsistent Deletions: {report.inconsistent_deletions_count}")
    print(f"Orphaned Temp Files:    {report.orphaned_temp_count}")
    print(f"Orphaned Unindexed:     {report.orphaned_unindexed_count}")
    print(f"Storage Mount Errors:   {report.storage_mount_errors_count}")
    print(f"Inaccessible Paths:     {report.inaccessible_paths_count}")
    print("=" * 75)

    if not report.has_anomalies:
        print("[OK] Zero discrepancies found. Database and filesystem are fully consistent.")
        print("=" * 75)
        return

    print("\nDetailed Discrepancy Inventory:")
    for idx, item in enumerate(report.items, 1):
        print(f"\n[{idx}] Category: {item.category.upper()}")
        if item.artifact_id:
            print(f"    Artifact ID:        {item.artifact_id}")
        if item.tenant_id:
            print(f"    Tenant ID:          {item.tenant_id}")
        if item.storage_path:
            print(f"    Storage Path:       {item.storage_path}")
        if item.status:
            print(f"    Status:             {item.status}")
        print(f"    Details:            {item.details}")
        print(f"    Recommended Action: {item.recommended_action}")

    print("\n" + "=" * 75)
    print("NOTICE: Dry-run mode completed. No files or database records were modified.")
    print("=" * 75)


async def main_async(args: argparse.Namespace) -> int:
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with session_factory() as session:
            report = await run_reconciliation(
                db=session,
                storage_root=Path(args.storage_dir) if args.storage_dir else None,
                stale_timeout_seconds=args.stale_minutes * 60,
                filter_tenant_id=args.tenant_id,
            )
            print_report(report, as_json=args.json)
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="MediaForge Media Artifact Reconciliation (Dry-Run)")
    parser.add_argument("--tenant-id", type=str, default=None, help="Filter audit to specific tenant ID")
    parser.add_argument("--storage-dir", type=str, default=None, help="Override storage root directory")
    parser.add_argument("--stale-minutes", type=int, default=30, help="Threshold in minutes for stale processing")
    parser.add_argument("--json", action="store_true", help="Output report as formatted JSON")
    args = parser.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
