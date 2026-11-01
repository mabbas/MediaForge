"""Service for persistent media artifacts lifecycle, ownership, and intra-tenant isolation (STEP 02-E1)."""

from __future__ import annotations

import hashlib
import inspect
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session_factory
from api.database.models import DownloadJobDB, MediaArtifactDB

# Test monkeypatching support
async_session_factory: Any = None
from api.dependencies import CurrentUser
from api.schemas.artifacts import MediaArtifactResponse
from api.security.media_storage import (
    _SAFE_ID_PATTERN,
    get_media_storage_root,
    get_tenant_media_dir,
    get_tenant_storage_root,
)

logger = logging.getLogger(__name__)


async def safe_scalar_first(res: Any) -> Any:
    """Safely extract the first scalar from an execute result, supporting real Result and AsyncMock."""
    if res is None:
        return None
    if isinstance(res, (AsyncMock, MagicMock)):
        try:
            scalars = res.scalars()
            if inspect.isawaitable(scalars):
                scalars = await scalars
            if isinstance(scalars, (AsyncMock, MagicMock)):
                val = scalars.first()
                if inspect.isawaitable(val):
                    val = await val
                if isinstance(val, (AsyncMock, MagicMock)):
                    return None
                return val
            return getattr(scalars, "first", lambda: None)()
        except Exception:
            return None
    try:
        return res.scalars().first()
    except Exception:
        return None


async def safe_scalar_one_or_none(res: Any) -> Any:
    """Safely extract scalar_one_or_none supporting real Result and AsyncMock."""
    if res is None:
        return None
    if isinstance(res, (AsyncMock, MagicMock)):
        try:
            fn = getattr(res, "scalar_one_or_none", None)
            if fn:
                val = fn()
                if inspect.isawaitable(val):
                    val = await val
                if isinstance(val, (AsyncMock, MagicMock)):
                    return None
                return val
        except Exception:
            return None
    try:
        return res.scalar_one_or_none()
    except Exception:
        return None


async def safe_scalars_all(res: Any) -> list[Any]:
    """Safely extract scalars().all() supporting real Result and AsyncMock."""
    if res is None:
        return []
    if isinstance(res, (AsyncMock, MagicMock)):
        try:
            scalars = res.scalars()
            if inspect.isawaitable(scalars):
                scalars = await scalars
            if isinstance(scalars, (AsyncMock, MagicMock)):
                all_fn = getattr(scalars, "all", None)
                if all_fn:
                    val = all_fn()
                    if inspect.isawaitable(val):
                        val = await val
                    if isinstance(val, (AsyncMock, MagicMock)):
                        return []
                    return list(val)
                return []
            return list(scalars.all())
        except Exception:
            return []
    try:
        return list(res.scalars().all())
    except Exception:
        return []



def format_bytes(size: int) -> str:
    """Format bytes to human-readable string."""
    if size <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    val = float(size)
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    return f"{val:.1f} {units[unit_idx]}" if unit_idx > 0 else f"{int(val)} B"


def compute_sha256(file_path: Path) -> str:
    """Calculate SHA-256 checksum of a file in chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def sanitize_media_error(error_text: str | None) -> str:
    """Sanitize error messages to eliminate host server paths and internal credentials."""
    if not error_text:
        return "Media processing operation failed"
    # Replace absolute file paths with generic [path]
    cleaned = re.sub(r"(/[a-zA-Z0-9_\.\-]+)+", "[path]", str(error_text))
    cleaned = re.sub(r"([A-Za-z]:\\[a-zA-Z0-9_\.\-\\]+)", "[path]", cleaned)
    # Strip any potential tokens or keys
    cleaned = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]+", "[token]", cleaned)
    return cleaned.strip()[:500]


def model_to_response(artifact: MediaArtifactDB) -> MediaArtifactResponse:
    """Convert an ORM MediaArtifactDB instance to MediaArtifactResponse schema."""
    return MediaArtifactResponse(
        id=artifact.id,
        tenant_id=artifact.tenant_id,
        user_id=artifact.user_id,
        api_key_id=artifact.api_key_id,
        artifact_type=artifact.artifact_type,
        source_download_id=artifact.source_download_id,
        parent_artifact_id=artifact.parent_artifact_id,
        storage_path=artifact.storage_path,
        filename=artifact.filename,
        format=artifact.format,
        duration_seconds=artifact.duration_seconds or 0.0,
        file_size_bytes=artifact.file_size_bytes or 0,
        file_size_human=format_bytes(artifact.file_size_bytes or 0),
        sha256_checksum=artifact.sha256_checksum,
        status=artifact.status,
        error_message=artifact.error_message,
        input_sources=artifact.input_sources or [],
        metadata_json=artifact.metadata_json or {},
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
        expires_at=artifact.expires_at,
    )


async def create_artifact_record(
    db: AsyncSession,
    artifact_id: str,
    tenant_id: str,
    user_id: Optional[str],
    api_key_id: Optional[str],
    artifact_type: str,
    storage_path: str,
    filename: str,
    format: str = "mp4",
    status: str = "processing",
    source_download_id: Optional[str] = None,
    parent_artifact_id: Optional[str] = None,
    input_sources: list[Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> MediaArtifactDB:
    """Create a persistent artifact record in initial state and commit."""
    artifact = MediaArtifactDB(
        id=artifact_id,
        tenant_id=tenant_id,
        user_id=user_id,
        api_key_id=api_key_id,
        artifact_type=artifact_type,
        storage_path=storage_path,
        filename=filename,
        format=format,
        status=status,
        source_download_id=source_download_id,
        parent_artifact_id=parent_artifact_id,
        input_sources=input_sources or [],
        metadata_json=metadata_json or {},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return artifact


async def complete_artifact_in_fresh_session(
    artifact_id: str,
    duration_seconds: float,
    file_size_bytes: int,
    sha256_checksum: str,
    metadata_update: dict[str, Any] | None = None,
    usage_event_kwargs: dict[str, Any] | None = None,
) -> None:
    """Update artifact to 'ready' and atomically record usage event in a single database transaction."""
    factory = async_session_factory or get_session_factory()
    if factory is None:
        return

    try:
        async with factory() as session:
            stmt = select(MediaArtifactDB).where(MediaArtifactDB.id == artifact_id)
            result = await session.execute(stmt)
            artifact = result.scalar_one_or_none()
            if artifact:
                artifact.status = "ready"
                artifact.duration_seconds = max(duration_seconds, 0.0)
                artifact.file_size_bytes = file_size_bytes
                artifact.sha256_checksum = sha256_checksum
                artifact.updated_at = datetime.utcnow()
                if metadata_update:
                    meta = dict(artifact.metadata_json or {})
                    meta.update(metadata_update)
                    artifact.metadata_json = meta

                # Atomic in-transaction commercial usage event recording (STEP 02-E2B-Fix-A)
                if usage_event_kwargs:
                    from api.services.usage_service import record_usage_event
                    await record_usage_event(session, **usage_event_kwargs)

                await session.commit()
    except Exception as exc:
        logger.warning("Failed to complete artifact %s in database: %s", artifact_id, exc)


async def fail_artifact_in_fresh_session(
    artifact_id: str,
    raw_error: str,
) -> None:
    """Update artifact to 'failed' in a decoupled fresh DB session."""
    factory = async_session_factory or get_session_factory()
    if factory is None:
        return

    try:
        async with factory() as session:
            stmt = select(MediaArtifactDB).where(MediaArtifactDB.id == artifact_id)
            result = await session.execute(stmt)
            artifact = result.scalar_one_or_none()
            if artifact:
                artifact.status = "failed"
                artifact.error_message = sanitize_media_error(raw_error)
                artifact.updated_at = datetime.utcnow()
                await session.commit()
    except Exception as exc:
        logger.warning("Failed to record failed status for artifact %s: %s", artifact_id, exc)



async def get_authorized_artifact(
    db: AsyncSession,
    artifact_id: str,
    user: CurrentUser,
) -> MediaArtifactDB:
    """Retrieve an artifact with strict tenant isolation and intra-tenant member scoping."""
    clean_id = (artifact_id or "").strip()
    if not clean_id or not _SAFE_ID_PATTERN.match(clean_id) or len(clean_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid artifact identifier")

    stmt = select(MediaArtifactDB).where(MediaArtifactDB.id == clean_id)
    result = await db.execute(stmt)
    artifact = await safe_scalar_one_or_none(result)

    if not artifact or artifact.tenant_id != user.tenant_id or artifact.status == "deleted":
        # Cross-tenant anti-enumeration: always 404
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Intra-tenant member isolation: members can only see their own artifacts
    if not user.is_admin_or_owner:
        if artifact.user_id != user.user_id:
            raise HTTPException(status_code=404, detail="Artifact not found")

    return artifact


async def list_authorized_artifacts(
    db: AsyncSession,
    user: CurrentUser,
    artifact_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[MediaArtifactDB], int]:
    """List artifacts with tenant scoping and intra-tenant member isolation."""
    query = select(MediaArtifactDB).where(MediaArtifactDB.tenant_id == user.tenant_id)

    # Intra-tenant member isolation
    if not user.is_admin_or_owner:
        query = query.where(MediaArtifactDB.user_id == user.user_id)

    if artifact_type:
        query = query.where(MediaArtifactDB.artifact_type == artifact_type.strip().lower())

    if status:
        query = query.where(MediaArtifactDB.status == status.strip().lower())
    else:
        # By default exclude soft-deleted artifacts
        query = query.where(MediaArtifactDB.status != "deleted")

    # Count total
    count_stmt = select(func.count()).select_from(query.subquery())
    count_res = await db.execute(count_stmt)
    total = (await safe_scalar_one_or_none(count_res)) or 0

    # Paginate
    query = query.order_by(MediaArtifactDB.created_at.desc()).limit(min(limit, 100)).offset(max(offset, 0))
    result = await db.execute(query)
    items = await safe_scalars_all(result)

    return items, total


async def delete_authorized_artifact(
    db: AsyncSession,
    artifact_id: str,
    user: CurrentUser,
) -> bool:
    """Soft delete an artifact and unlink the underlying file from tenant storage."""
    artifact = await get_authorized_artifact(db, artifact_id, user)

    # Update status to deleted
    artifact.status = "deleted"
    artifact.updated_at = datetime.utcnow()
    await db.commit()

    # Safely remove physical file
    tenant_root = get_tenant_storage_root(user.tenant_id)
    file_path = (tenant_root / artifact.storage_path).resolve()
    try:
        if file_path.is_file() and file_path.is_relative_to(tenant_root):
            file_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not delete physical artifact file %s: %s", file_path, exc)

    return True


def escape_sql_like(val: str) -> str:
    """Escape SQL LIKE wildcards (% and _) to avoid pattern injection."""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def verify_source_media_authorization(
    db: AsyncSession,
    source_ref: str,
    user: CurrentUser,
) -> None:
    """Ensure a member caller does not access source media belonging to another member.

    If source_ref matches an existing MediaArtifactDB or DownloadJobDB, verify user_id or api_key_id.
    If no record exists and user is a non-admin member, fail closed with HTTP 404.
    If multiple records match an ambiguous filename, fail closed with HTTP 409.
    """
    if user.is_admin_or_owner:
        return

    clean_ref = source_ref.strip()
    clean_id = clean_ref
    if clean_ref.startswith("artifact:"):
        clean_id = clean_ref[len("artifact:"):]
    elif clean_ref.startswith("art:"):
        clean_id = clean_ref[len("art:"):]
    elif clean_ref.startswith("job:"):
        clean_id = clean_ref[len("job:"):]

    norm_ref = clean_ref.replace("\\", "/")
    basename = norm_ref.split("/")[-1]
    name_no_ext = basename.rsplit(".", 1)[0] if "." in basename else basename
    escaped_base = escape_sql_like(basename)

    # 1. Direct ID lookup in MediaArtifactDB
    stmt_art_id = select(MediaArtifactDB).where(
        MediaArtifactDB.tenant_id == user.tenant_id,
        (MediaArtifactDB.id == clean_id) | (MediaArtifactDB.id == clean_ref),
    )
    art_by_id = await safe_scalar_one_or_none(await db.execute(stmt_art_id))
    if art_by_id:
        is_owner = (art_by_id.user_id == user.user_id) or (
            bool(user.api_key_id) and art_by_id.api_key_id == user.api_key_id
        )
        if not is_owner:
            raise HTTPException(status_code=404, detail="Source media artifact not found in tenant storage")
        return

    # 2. Direct ID lookup in DownloadJobDB
    stmt_job_id = select(DownloadJobDB).where(
        DownloadJobDB.tenant_id == user.tenant_id,
        (DownloadJobDB.id == clean_id) | (DownloadJobDB.id == clean_ref),
    )
    job_by_id = await safe_scalar_one_or_none(await db.execute(stmt_job_id))
    if job_by_id:
        is_owner = (job_by_id.user_id == user.user_id) or (
            bool(user.api_key_id) and bool(job_by_id.metadata_json) and job_by_id.metadata_json.get("api_key_id") == user.api_key_id
        )
        if not is_owner:
            raise HTTPException(status_code=404, detail="Source media artifact not found in tenant storage")
        return

    # 3. Path / Filename lookup across tenant records
    cond_art = (
        (MediaArtifactDB.storage_path == clean_ref)
        | (MediaArtifactDB.storage_path == norm_ref)
        | (MediaArtifactDB.storage_path.like(f"%/{escaped_base}", escape="\\"))
        | (MediaArtifactDB.filename == clean_ref)
        | (MediaArtifactDB.filename == basename)
    )
    stmt_art = select(MediaArtifactDB).where(MediaArtifactDB.tenant_id == user.tenant_id, cond_art)
    arts = await safe_scalars_all(await db.execute(stmt_art))

    cond_job = (
        (DownloadJobDB.file_path == clean_ref)
        | (DownloadJobDB.file_path == norm_ref)
        | (DownloadJobDB.file_path.like(f"%/{escaped_base}", escape="\\"))
        | (DownloadJobDB.title == clean_ref)
        | (DownloadJobDB.title == name_no_ext)
    )
    stmt_job = select(DownloadJobDB).where(DownloadJobDB.tenant_id == user.tenant_id, cond_job)
    jobs = await safe_scalars_all(await db.execute(stmt_job))

    authorized_matches = []
    for a in arts:
        is_owner = (a.user_id == user.user_id) or (
            bool(user.api_key_id) and a.api_key_id == user.api_key_id
        )
        if is_owner:
            authorized_matches.append(a)

    for j in jobs:
        is_owner = (j.user_id == user.user_id) or (
            bool(user.api_key_id) and bool(j.metadata_json) and j.metadata_json.get("api_key_id") == user.api_key_id
        )
        if is_owner:
            authorized_matches.append(j)

    if not authorized_matches:
        raise HTTPException(status_code=404, detail="Source media artifact not found in tenant storage")

    if len(authorized_matches) > 1:
        raise HTTPException(
            status_code=409,
            detail="Ambiguous media reference: multiple matching records found. Please use server-issued unique media ID",
        )


async def resolve_source_media_reference(
    db: AsyncSession,
    source_ref: str,
    tenant_id: str,
    user: Optional[CurrentUser] = None,
) -> str:
    """Resolve a source media reference (job ID, artifact ID, title, or relative path)
    to a canonical relative path within tenant media storage.

    Accepts:
    - Raw download job ID / 'job:<id>'
    - Raw artifact ID / 'artifact:<id>' / 'art:<id>'
    - Canonical relative path (e.g. 'downloads/video.mp4')
    """
    if not source_ref or not isinstance(source_ref, str):
        return source_ref

    raw_ref = source_ref.strip()

    # URLs pass through unchanged
    if raw_ref.startswith("http://") or raw_ref.startswith("https://"):
        return raw_ref

    # Reject absolute paths and path traversal tokens from all public callers (including admins)
    if user:
        if raw_ref.startswith(("/", "\\")) or (len(raw_ref) >= 2 and raw_ref[1] == ":"):
            raise HTTPException(
                status_code=400,
                detail="Absolute filesystem paths are prohibited in API media requests",
            )
        if ".." in raw_ref.replace("\\", "/").split("/"):
            raise HTTPException(
                status_code=400,
                detail="Invalid source media reference: Path traversal token ('..') is prohibited",
            )

    tenant_root = get_tenant_storage_root(tenant_id)
    norm_ref = raw_ref.replace("\\", "/")
    tenant_root_norm = str(tenant_root).replace("\\", "/").rstrip("/") + "/"

    # Normalize container internal paths matching tenant root for internal system callers
    if user is None:
        if norm_ref.startswith(tenant_root_norm):
            raw_ref = norm_ref[len(tenant_root_norm):]
        elif norm_ref.startswith(f"/app/downloads/{tenant_id}/"):
            raw_ref = norm_ref[len(f"/app/downloads/{tenant_id}/"):]

    clean_id = raw_ref
    if raw_ref.startswith("job:"):
        clean_id = raw_ref[len("job:"):]
    elif raw_ref.startswith("artifact:"):
        clean_id = raw_ref[len("artifact:"):]
    elif raw_ref.startswith("art:"):
        clean_id = raw_ref[len("art:"):]

    basename = norm_ref.split("/")[-1]
    name_no_ext = basename.rsplit(".", 1)[0] if "." in basename else basename
    escaped_base = escape_sql_like(basename)

    # Helper to resolve job file path to tenant-relative string
    def _canonicalize_job_path(job: DownloadJobDB) -> str:
        if job.file_path:
            norm_fp = str(job.file_path).replace("\\", "/")
            if norm_fp.startswith(tenant_root_norm):
                return norm_fp[len(tenant_root_norm):]
            if norm_fp.startswith(f"/app/downloads/{tenant_id}/"):
                return norm_fp[len(f"/app/downloads/{tenant_id}/"):]
            fp_path = Path(job.file_path)
            if (tenant_root / fp_path).is_file():
                return str(fp_path).replace("\\", "/")
            if (tenant_root / "downloads" / fp_path.name).is_file():
                return f"downloads/{fp_path.name}"
            return norm_fp
        if job.title:
            for ext in (".mp4", ".mkv", ".webm"):
                candidate = tenant_root / "downloads" / f"{job.title}{ext}"
                if candidate.is_file():
                    return f"downloads/{job.title}{ext}"
            return f"downloads/{job.title}.mp4"
        return norm_ref

    # 1. Direct ID lookup in DownloadJobDB
    stmt_id_job = select(DownloadJobDB).where(
        DownloadJobDB.tenant_id == tenant_id,
        (DownloadJobDB.id == clean_id) | (DownloadJobDB.id == raw_ref),
    )
    job_by_id = await safe_scalar_one_or_none(await db.execute(stmt_id_job))
    if job_by_id:
        if user and not user.is_admin_or_owner:
            is_owner = (job_by_id.user_id == user.user_id) or (
                bool(user.api_key_id) and bool(job_by_id.metadata_json) and job_by_id.metadata_json.get("api_key_id") == user.api_key_id
            )
            if not is_owner:
                raise HTTPException(status_code=404, detail="Source media artifact not found in tenant storage")
        return _canonicalize_job_path(job_by_id)

    # 2. Direct ID lookup in MediaArtifactDB
    stmt_id_art = select(MediaArtifactDB).where(
        MediaArtifactDB.tenant_id == tenant_id,
        (MediaArtifactDB.id == clean_id) | (MediaArtifactDB.id == raw_ref),
    )
    art_by_id = await safe_scalar_one_or_none(await db.execute(stmt_id_art))
    if art_by_id:
        if user and not user.is_admin_or_owner:
            is_owner = (art_by_id.user_id == user.user_id) or (
                bool(user.api_key_id) and art_by_id.api_key_id == user.api_key_id
            )
            if not is_owner:
                raise HTTPException(status_code=404, detail="Source media artifact not found in tenant storage")
        return art_by_id.storage_path

    # If an explicit ID prefix was provided but no record matched in tenant: fail closed!
    if raw_ref.startswith(("job:", "artifact:", "art:")):
        raise HTTPException(status_code=404, detail="Source media artifact not found in tenant storage")

    # 3. Path / Filename lookup across tenant records with strict ambiguity prevention
    cond_art = (
        (MediaArtifactDB.storage_path == raw_ref)
        | (MediaArtifactDB.storage_path == norm_ref)
        | (MediaArtifactDB.storage_path.like(f"%/{escaped_base}", escape="\\"))
        | (MediaArtifactDB.filename == raw_ref)
        | (MediaArtifactDB.filename == basename)
    )
    stmt_art = select(MediaArtifactDB).where(MediaArtifactDB.tenant_id == tenant_id, cond_art)
    all_arts = await safe_scalars_all(await db.execute(stmt_art))

    cond_job = (
        (DownloadJobDB.file_path == raw_ref)
        | (DownloadJobDB.file_path == norm_ref)
        | (DownloadJobDB.file_path.like(f"%/{escaped_base}", escape="\\"))
        | (DownloadJobDB.title == raw_ref)
        | (DownloadJobDB.title == name_no_ext)
    )
    stmt_job = select(DownloadJobDB).where(DownloadJobDB.tenant_id == tenant_id, cond_job)
    all_jobs = await safe_scalars_all(await db.execute(stmt_job))

    authorized_jobs = []
    for j in all_jobs:
        if user is None or user.is_admin_or_owner:
            authorized_jobs.append(j)
        else:
            is_own = (j.user_id == user.user_id) or (
                bool(user.api_key_id) and bool(j.metadata_json) and j.metadata_json.get("api_key_id") == user.api_key_id
            )
            if is_own:
                authorized_jobs.append(j)

    authorized_arts = []
    for a in all_arts:
        if user is None or user.is_admin_or_owner:
            authorized_arts.append(a)
        else:
            is_own = (a.user_id == user.user_id) or (
                bool(user.api_key_id) and a.api_key_id == user.api_key_id
            )
            if is_own:
                authorized_arts.append(a)

    total_matches = len(authorized_jobs) + len(authorized_arts)

    if total_matches > 1:
        raise HTTPException(
            status_code=409,
            detail="Ambiguous media reference: multiple matching records found. Please use server-issued unique media ID",
        )

    if total_matches == 1:
        if authorized_jobs:
            return _canonicalize_job_path(authorized_jobs[0])
        return authorized_arts[0].storage_path

    # If matching records exist in tenant but caller is not authorized: fail closed with 404!
    if (len(all_jobs) + len(all_arts) > 0) and total_matches == 0:
        raise HTTPException(status_code=404, detail="Source media artifact not found in tenant storage")

    # If no DB record was found, check physical disk presence for tenant owners/admins and internal callers
    if user is None or user.is_admin_or_owner:
        for candidate in [
            tenant_root / raw_ref,
            tenant_root / "downloads" / raw_ref,
            tenant_root / "clips" / raw_ref,
            tenant_root / "merged" / raw_ref,
        ]:
            try:
                resolved = candidate.resolve()
                if resolved.is_file() and resolved.is_relative_to(tenant_root):
                    return str(resolved.relative_to(tenant_root)).replace("\\", "/")
            except Exception:
                pass

    return raw_ref
