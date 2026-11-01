"""MediaForge Media Retention Foundation & Preview Service (STEP 02-E2A).

Provides tenant-aware, strictly read-only retention calculation and preview audit.

Core Invariants:
1. Default Retain-Forever Policy: Customer media has NO default expiration (retention_days = -1).
   Customer files are NEVER marked for cleanup under the default policy.
2. Download-Job Protection: Any media referenced by active or historical DownloadJobDB records
   is permanently protected from cleanup.
3. Referenced-Media Protection: Any artifact referenced as an input source or parent by another
   artifact (input_sources, parent_artifact_id, source_download_id) is permanently protected.
4. Historical Anomaly Segregation: The 10 known pre-Fix-B test artifacts are classified separately
   under 'historical_test_anomaly' and their database records are never modified.
5. Strict Read-Only Dry Run: This service performs ZERO deletions and ZERO mutations of database records.
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

# Default policy: Customer media is retained indefinitely (-1 days)
DEFAULT_RETENTION_DAYS = int(os.environ.get("MEDIAFORGE_DEFAULT_RETENTION_DAYS", "-1"))

# The 10 known historical test artifacts identified during STEP 02-E1 development/testing
HISTORICAL_TEST_ANOMALIES: frozenset[str] = frozenset({
    # 1 Stale processing clip from 2026-10-18 17:47 UTC
    "f76f7f6cc0c0",
    # 2 Seeded test fixture clips (spaces / quotes test)
    "00000000-0000-0000-0000-0000000000a1",
    "00000000-0000-0000-0000-0000000000a2",
    # 7 Historical test merges whose backing files were unlinked during pre-Fix-B test teardown
    "e039ebc704ea",
    "76ca05ebcf6d",
    "9c5f7e065128",
    "addeb512c3c2",
    "ba1aa7edf321",
    "73260f24b552",
    "bf6b4141a7dd",
})


@dataclass
class RetentionItem:
    """Represents a single evaluated artifact or storage item."""
    artifact_id: Optional[str]
    tenant_id: str
    user_id: Optional[str]
    storage_path: str
    file_size_bytes: int
    created_at: Optional[str]
    status: Optional[str]
    classification: str
    action: str
    reason: str
    is_customer_media: bool
    is_protected: bool
    eligible_for_cleanup: bool


@dataclass
class RetentionPreviewReport:
    """Complete summary and itemized breakdown of dry-run retention audit."""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    storage_root: str = ""
    policy: str = "retain_forever"
    default_retention_days: int = DEFAULT_RETENTION_DAYS
    effective_retention_days: int = DEFAULT_RETENTION_DAYS
    filter_tenant_id: Optional[str] = None

    # High-level metrics
    total_scanned_artifacts: int = 0
    total_scanned_files: int = 0
    customer_media_retained_count: int = 0
    download_job_protected_count: int = 0
    referenced_source_protected_count: int = 0
    historical_anomalies_count: int = 0
    already_deleted_count: int = 0
    eligible_for_cleanup_count: int = 0
    estimated_reclaimable_bytes: int = 0

    tenant_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    items: list[RetentionItem] = field(default_factory=list)


async def generate_retention_preview(
    db: AsyncSession,
    storage_root: Path | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    filter_tenant_id: str | None = None,
) -> RetentionPreviewReport:
    """Generate a strictly read-only retention and cleanup preview report.

    Guarantees:
    - ZERO deletions performed.
    - ZERO database rows modified.
    - Customer media protected under default retain-forever policy.
    - Download jobs and parent references protected.
    - Historical test anomalies classified distinctly.
    """
    if storage_root is not None:
        if not isinstance(storage_root, Path):
            storage_root = Path(storage_root)
        if not storage_root.exists() or not storage_root.is_dir():
            raise FileNotFoundError(f"Storage root '{storage_root}' does not exist or is not a directory")

    base_root = (storage_root or get_media_storage_root()).resolve()
    policy_name = "retain_forever" if retention_days < 0 else f"explicit_{retention_days}_days"

    report = RetentionPreviewReport(
        storage_root=str(base_root),
        policy=policy_name,
        default_retention_days=DEFAULT_RETENTION_DAYS,
        effective_retention_days=retention_days,
        filter_tenant_id=filter_tenant_id,
    )

    now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(days=retention_days) if retention_days >= 0 else None

    # 1. Fetch all download jobs to establish protected source paths and job IDs
    dj_query = select(DownloadJobDB)
    if filter_tenant_id:
        dj_query = dj_query.where(DownloadJobDB.tenant_id == filter_tenant_id)
    dj_res = await db.execute(dj_query)
    download_jobs = list(dj_res.scalars().all())

    protected_download_job_ids: set[str] = {dj.id for dj in download_jobs if dj.id}
    protected_download_paths: set[tuple[str, str]] = set()
    for dj in download_jobs:
        if dj.tenant_id and dj.file_path:
            norm_p = str(dj.file_path).replace("\\", "/").lstrip("/")
            protected_download_paths.add((dj.tenant_id, norm_p))

    # 2. Fetch all media artifacts
    art_query = select(MediaArtifactDB)
    if filter_tenant_id:
        art_query = art_query.where(MediaArtifactDB.tenant_id == filter_tenant_id)
    art_res = await db.execute(art_query)
    artifacts = list(art_res.scalars().all())
    report.total_scanned_artifacts = len(artifacts)

    # 3. Identify all artifacts referenced as inputs or parents by other artifacts
    referenced_artifact_ids: set[str] = set()
    referenced_storage_paths: set[tuple[str, str]] = set()

    for art in artifacts:
        if art.parent_artifact_id:
            referenced_artifact_ids.add(art.parent_artifact_id)
        if art.source_download_id:
            protected_download_job_ids.add(art.source_download_id)

        # Check input_sources json array
        if art.input_sources and isinstance(art.input_sources, list):
            for src in art.input_sources:
                if isinstance(src, str):
                    referenced_storage_paths.add((art.tenant_id, src.replace("\\", "/").lstrip("/")))

    # 4. Evaluate each MediaArtifactDB record
    for art in artifacts:
        t_id = art.tenant_id
        if t_id not in report.tenant_summaries:
            report.tenant_summaries[t_id] = {
                "total_artifacts": 0,
                "retained": 0,
                "protected": 0,
                "historical_anomalies": 0,
                "eligible_cleanup": 0,
                "reclaimable_bytes": 0,
            }
        t_summary = report.tenant_summaries[t_id]
        t_summary["total_artifacts"] += 1

        rel_path = str(art.storage_path).replace("\\", "/").lstrip("/")
        norm_key = (t_id, rel_path)
        art_created_str = art.created_at.isoformat() if art.created_at else None

        # Check 1: Known historical test anomalies (unmodified preservation)
        if art.id in HISTORICAL_TEST_ANOMALIES:
            report.historical_anomalies_count += 1
            t_summary["historical_anomalies"] += 1
            report.items.append(
                RetentionItem(
                    artifact_id=art.id,
                    tenant_id=t_id,
                    user_id=art.user_id,
                    storage_path=rel_path,
                    file_size_bytes=art.file_size_bytes or 0,
                    created_at=art_created_str,
                    status=art.status,
                    classification="historical_test_anomaly",
                    action="PRESERVE_UNMODIFIED",
                    reason="Known pre-Fix-B test anomaly; record preserved unmodified without altering live DB state",
                    is_customer_media=False,
                    is_protected=True,
                    eligible_for_cleanup=False,
                )
            )
            continue

        # Check 2: In-progress artifacts (pending/processing) are excluded from cleanup
        if art.status in ("pending", "processing"):
            t_summary["protected"] += 1
            report.items.append(
                RetentionItem(
                    artifact_id=art.id,
                    tenant_id=t_id,
                    user_id=art.user_id,
                    storage_path=rel_path,
                    file_size_bytes=art.file_size_bytes or 0,
                    created_at=art_created_str,
                    status=art.status,
                    classification="in_progress_excluded",
                    action="EXCLUDE_IN_PROGRESS",
                    reason="Artifact is currently in progress (pending or processing); excluded from retention cleanup",
                    is_customer_media=True,
                    is_protected=True,
                    eligible_for_cleanup=False,
                )
            )
            continue

        # Check 2: Download job protected media
        is_dj_backed = (
            (art.source_download_id in protected_download_job_ids)
            or (norm_key in protected_download_paths)
        )
        if is_dj_backed:
            report.download_job_protected_count += 1
            t_summary["protected"] += 1
            report.items.append(
                RetentionItem(
                    artifact_id=art.id,
                    tenant_id=t_id,
                    user_id=art.user_id,
                    storage_path=rel_path,
                    file_size_bytes=art.file_size_bytes or 0,
                    created_at=art_created_str,
                    status=art.status,
                    classification="download_job_protected",
                    action="PRESERVE_PROTECTED",
                    reason="Backed by authoritative download job; permanently retained from cleanup",
                    is_customer_media=True,
                    is_protected=True,
                    eligible_for_cleanup=False,
                )
            )
            continue

        # Check 3: Referenced as input source or parent by derived artifacts
        is_referenced = (
            (art.id in referenced_artifact_ids)
            or (norm_key in referenced_storage_paths)
            or ((t_id, art.filename) in referenced_storage_paths)
        )
        if is_referenced:
            report.referenced_source_protected_count += 1
            t_summary["protected"] += 1
            report.items.append(
                RetentionItem(
                    artifact_id=art.id,
                    tenant_id=t_id,
                    user_id=art.user_id,
                    storage_path=rel_path,
                    file_size_bytes=art.file_size_bytes or 0,
                    created_at=art_created_str,
                    status=art.status,
                    classification="referenced_source_protected",
                    action="PRESERVE_PROTECTED",
                    reason="Referenced as input source or parent for derived media; protected from cleanup",
                    is_customer_media=True,
                    is_protected=True,
                    eligible_for_cleanup=False,
                )
            )
            continue

        # Check 4: Already soft-deleted artifacts
        if art.status == "deleted":
            report.already_deleted_count += 1
            report.items.append(
                RetentionItem(
                    artifact_id=art.id,
                    tenant_id=t_id,
                    user_id=art.user_id,
                    storage_path=rel_path,
                    file_size_bytes=art.file_size_bytes or 0,
                    created_at=art_created_str,
                    status=art.status,
                    classification="already_soft_deleted",
                    action="PRESERVE_RECORD",
                    reason="Artifact record is already marked 'deleted'; preserved for audit integrity",
                    is_customer_media=True,
                    is_protected=False,
                    eligible_for_cleanup=False,
                )
            )
            continue

        # Check 5: Default Retain-Forever Policy (-1)
        if retention_days < 0:
            report.customer_media_retained_count += 1
            t_summary["retained"] += 1
            report.items.append(
                RetentionItem(
                    artifact_id=art.id,
                    tenant_id=t_id,
                    user_id=art.user_id,
                    storage_path=rel_path,
                    file_size_bytes=art.file_size_bytes or 0,
                    created_at=art_created_str,
                    status=art.status,
                    classification="customer_media_retained",
                    action="RETAIN",
                    reason="Default retain-forever policy applies; customer media is preserved indefinitely",
                    is_customer_media=True,
                    is_protected=False,
                    eligible_for_cleanup=False,
                )
            )
            continue

        # Check 6: Explicit retention policy evaluation (opt-in simulated window)
        art_time = art.created_at.replace(tzinfo=timezone.utc) if (art.created_at and art.created_at.tzinfo is None) else (art.created_at or now)
        if cutoff_time and art_time < cutoff_time:
            report.eligible_for_cleanup_count += 1
            report.estimated_reclaimable_bytes += (art.file_size_bytes or 0)
            t_summary["eligible_cleanup"] += 1
            t_summary["reclaimable_bytes"] += (art.file_size_bytes or 0)
            report.items.append(
                RetentionItem(
                    artifact_id=art.id,
                    tenant_id=t_id,
                    user_id=art.user_id,
                    storage_path=rel_path,
                    file_size_bytes=art.file_size_bytes or 0,
                    created_at=art_created_str,
                    status=art.status,
                    classification="expired_retention_candidate",
                    action="PREVIEW_CLEANUP",
                    reason=f"Exceeds explicit retention threshold of {retention_days} days (DRY RUN PREVIEW ONLY - NO ACTION TAKEN)",
                    is_customer_media=True,
                    is_protected=False,
                    eligible_for_cleanup=True,
                )
            )
        else:
            report.customer_media_retained_count += 1
            t_summary["retained"] += 1
            report.items.append(
                RetentionItem(
                    artifact_id=art.id,
                    tenant_id=t_id,
                    user_id=art.user_id,
                    storage_path=rel_path,
                    file_size_bytes=art.file_size_bytes or 0,
                    created_at=art_created_str,
                    status=art.status,
                    classification="active_retention_window",
                    action="RETAIN",
                    reason=f"Within active retention window of {retention_days} days",
                    is_customer_media=True,
                    is_protected=False,
                    eligible_for_cleanup=False,
                )
            )

    # 5. Scan filesystem for physical file accounting
    if base_root.exists() and base_root.is_dir():
        try:
            tenant_dirs = [d for d in base_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
            if filter_tenant_id:
                tenant_dirs = [d for d in tenant_dirs if d.name == filter_tenant_id]

            for t_dir in tenant_dirs:
                for f in t_dir.rglob("*"):
                    if f.is_file():
                        report.total_scanned_files += 1
        except Exception as exc:
            logger.warning("Filesystem scanning encountered error: %s", exc)

    if filter_tenant_id:
        report.tenant_summaries = {k: v for k, v in report.tenant_summaries.items() if k == filter_tenant_id}

    return report


def print_retention_preview(report: RetentionPreviewReport, as_json: bool = False) -> None:
    """Format and print the retention preview report."""
    if as_json:
        print(json.dumps(asdict(report), indent=2))
        return

    print("=" * 80)
    print(" MediaForge Customer Media Retention & Cleanup Foundation (STEP 02-E2A)")
    print("=" * 80)
    print(f"Timestamp:                    {report.timestamp}")
    print(f"Storage Base Root:            {report.storage_root}")
    print(f"Active Retention Policy:      {report.policy.upper()}")
    print(f"Default Policy Retention:     {'RETAIN FOREVER (-1)' if report.default_retention_days < 0 else f'{report.default_retention_days} days'}")
    print(f"Effective Evaluation Window:  {'RETAIN FOREVER (-1)' if report.effective_retention_days < 0 else f'{report.effective_retention_days} days'}")
    print(f"Filter Tenant:                {report.filter_tenant_id or 'ALL TENANTS'}")
    print("-" * 80)
    print(f"Total Scanned DB Artifacts:   {report.total_scanned_artifacts}")
    print(f"Total Scanned Storage Files:  {report.total_scanned_files}")
    print("-" * 80)
    print(f"Customer Media Retained:      {report.customer_media_retained_count} records (100% preserved)")
    print(f"Download Job Files Protected: {report.download_job_protected_count} records")
    print(f"Referenced Sources Protected: {report.referenced_source_protected_count} records")
    print(f"Historical Anomalies Isolated:{report.historical_anomalies_count} records (unmodified)")
    print(f"Already Soft-Deleted:         {report.already_deleted_count} records")
    print("-" * 80)
    print(f"Eligible for Cleanup:         {report.eligible_for_cleanup_count} items")
    print(f"Estimated Reclaimable Space:  {report.estimated_reclaimable_bytes} bytes")
    print("=" * 80)

    if report.eligible_for_cleanup_count == 0:
        print("[POLICY ENFORCED] Default retain-forever policy is active. ZERO customer files are eligible for cleanup.")
    else:
        print(f"[PREVIEW NOTICE] {report.eligible_for_cleanup_count} items meet hypothetical expiration criteria (DRY-RUN ONLY - NO DELETIONS PERFORMED).")

    print("\nTenant Breakdown:")
    for t_id, summary in report.tenant_summaries.items():
        print(f"  Tenant {t_id}:")
        print(f"    Total: {summary['total_artifacts']} | Retained: {summary['retained']} | Protected: {summary['protected']} | Historical: {summary['historical_anomalies']} | Cleanup Preview: {summary['eligible_cleanup']}")

    print("\nDetailed Item Classification Preview:")
    for idx, item in enumerate(report.items, 1):
        print(f"\n[{idx}] Artifact ID:        {item.artifact_id or 'N/A'}")
        print(f"    Tenant ID:          {item.tenant_id}")
        print(f"    Storage Path:       {item.storage_path}")
        print(f"    Classification:     {item.classification.upper()}")
        print(f"    Action:             {item.action}")
        print(f"    Reason:             {item.reason}")

    print("\n" + "=" * 80)
    print("STRICT INVARIANCE NOTICE: Dry-run preview completed. Zero database records or physical media files were altered.")
    print("=" * 80)


async def main_async(args: argparse.Namespace) -> int:
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with session_factory() as session:
            report = await generate_retention_preview(
                db=session,
                storage_root=Path(args.storage_dir) if args.storage_dir else None,
                retention_days=args.retention_days,
                filter_tenant_id=args.tenant_id,
            )
            print_retention_preview(report, as_json=args.json)
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="MediaForge Media Retention & Cleanup Foundation (Dry-Run Preview)")
    parser.add_argument("--tenant-id", type=str, default=None, help="Filter retention preview to specific tenant ID")
    parser.add_argument("--storage-dir", type=str, default=None, help="Override storage root directory")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS, help="Retention window in days (-1 for retain-forever default)")
    parser.add_argument("--json", action="store_true", help="Output report as formatted JSON")
    args = parser.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
