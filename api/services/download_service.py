"""Download job persistence and lookup — sync between engine and database."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.models import DownloadJobDB
from src.models.download import DownloadJob, DownloadRequest
from src.models.enums import DownloadStatus


async def get_job_from_db(
    session: AsyncSession,
    job_id: str,
    tenant_id: str | None = None,
) -> DownloadJobDB | None:
    """Get a download job by id, optionally scoped by tenant."""
    query = select(DownloadJobDB).where(DownloadJobDB.id == job_id)
    if tenant_id and tenant_id != "default":
        query = query.where(DownloadJobDB.tenant_id == tenant_id)
    result = await session.execute(query)
    return result.scalars().one_or_none()


async def update_job_status(
    session: AsyncSession,
    job_id: str,
    status: str,
    progress_percent: float | None = None,
    speed_bps: float | None = None,
    eta_seconds: int | None = None,
    bytes_downloaded: int | None = None,
    total_bytes: int | None = None,
    error_message: str | None = None,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
) -> None:
    """Update a job's status and progress in the database."""
    result = await session.execute(select(DownloadJobDB).where(DownloadJobDB.id == job_id))
    row = result.scalars().one_or_none()
    if not row:
        return
    row.status = status
    if progress_percent is not None:
        row.progress_percent = progress_percent
    if speed_bps is not None:
        row.speed_bps = speed_bps
    if eta_seconds is not None:
        row.eta_seconds = eta_seconds
    if bytes_downloaded is not None:
        row.bytes_downloaded = bytes_downloaded
    if total_bytes is not None:
        row.total_bytes = total_bytes
    if error_message is not None:
        row.error_message = error_message
    if file_path is not None:
        row.file_path = file_path
    if file_size_bytes is not None:
        row.file_size_bytes = file_size_bytes
    if status in ("completed", "failed", "cancelled"):
        row.completed_at = datetime.utcnow()
    session.add(row)


async def persist_job(
    session: AsyncSession,
    job: DownloadJob,
    user_id: str = "system",
    tenant_id: str = "default",
) -> DownloadJobDB:
    """Create a DownloadJobDB from an in-memory DownloadJob."""
    provider = "generic"
    if job.request and job.request.url:
        if "youtube" in job.request.url or "youtu.be" in job.request.url:
            provider = "youtube"
        elif "facebook" in job.request.url:
            provider = "facebook"

    media_type = "video"
    if job.request and hasattr(job.request, "media_type"):
        media_type = getattr(job.request.media_type, "value", str(job.request.media_type))

    quality = None
    if job.request and hasattr(job.request, "quality"):
        quality = getattr(job.request.quality, "value", str(job.request.quality))

    url = job.request.url if job.request else ""
    status = job.progress.status.value if job.progress else "created"

    file_path = None
    file_size_bytes = None
    title = None
    if job.result:
        file_path = getattr(job.result, "file_path", None)
        file_size_bytes = getattr(job.result, "file_size_bytes", None)
        title = getattr(job.result, "title", None)

    db_job = DownloadJobDB(
        id=job.job_id,
        url=url,
        provider=provider,
        media_type=media_type,
        status=status,
        priority=job.priority,
        title=title,
        quality=quality,
        file_path=file_path,
        file_size_bytes=file_size_bytes,
        progress_percent=job.progress.percent if job.progress else 0.0,
        speed_bps=job.progress.speed_bytes_per_second if job.progress else 0.0,
        eta_seconds=job.progress.eta_seconds if job.progress else None,
        bytes_downloaded=job.progress.bytes_downloaded if job.progress else 0,
        total_bytes=job.progress.total_bytes if job.progress else None,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    session.add(db_job)
    return db_job


def job_to_response(job: DownloadJob) -> dict:
    """Map in-memory DownloadJob to API response dict."""
    progress = job.progress
    return {
        "job_id": job.job_id,
        "status": progress.status.value if progress else "created",
        "url": job.request.url if job.request else "",
        "progress_percent": progress.percent if progress else 0.0,
        "priority": job.priority,
        "speed_human": progress.speed_human if progress else None,
        "eta_human": progress.eta_human if progress else None,
        "title": job.title or (job.result.title if job.result else None),
    }
