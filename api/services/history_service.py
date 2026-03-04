"""Download history service — queries and aggregates download history from database."""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.models import DownloadJobDB
from src.cli.console import format_size

logger = logging.getLogger(__name__)


async def get_history(
    session: AsyncSession,
    tenant_id: str | None = None,
    user_id: str | None = None,
    status: str | None = None,
    media_type: str | None = None,
    provider: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[DownloadJobDB], int]:
    """Get paginated download history."""
    query = select(DownloadJobDB)
    count_query = select(func.count(DownloadJobDB.id))

    if tenant_id and tenant_id != "default":
        query = query.where(DownloadJobDB.tenant_id == tenant_id)
        count_query = count_query.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        query = query.where(DownloadJobDB.user_id == user_id)
        count_query = count_query.where(DownloadJobDB.user_id == user_id)
    if status:
        query = query.where(DownloadJobDB.status == status)
        count_query = count_query.where(DownloadJobDB.status == status)
    if media_type:
        query = query.where(DownloadJobDB.media_type == media_type)
        count_query = count_query.where(DownloadJobDB.media_type == media_type)
    if provider:
        query = query.where(DownloadJobDB.provider == provider)
        count_query = count_query.where(DownloadJobDB.provider == provider)
    if search:
        search_filter = f"%{search}%"
        query = query.where(
            or_(
                DownloadJobDB.title.ilike(search_filter),
                DownloadJobDB.url.ilike(search_filter),
            )
        )
        count_query = count_query.where(
            or_(
                DownloadJobDB.title.ilike(search_filter),
                DownloadJobDB.url.ilike(search_filter),
            )
        )

    query = query.order_by(DownloadJobDB.created_at.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await session.execute(query)
    jobs = list(result.scalars().all())

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    return jobs, total


async def get_history_stats(
    session: AsyncSession,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """Get aggregated download history stats."""
    total_q = select(func.count(DownloadJobDB.id))
    if tenant_id and tenant_id != "default":
        total_q = total_q.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        total_q = total_q.where(DownloadJobDB.user_id == user_id)
    total_result = await session.execute(total_q)
    total = total_result.scalar() or 0

    status_q = (
        select(DownloadJobDB.status, func.count(DownloadJobDB.id)).group_by(
            DownloadJobDB.status
        )
    )
    if tenant_id and tenant_id != "default":
        status_q = status_q.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        status_q = status_q.where(DownloadJobDB.user_id == user_id)
    status_result = await session.execute(status_q)
    by_status = dict(status_result.all())

    provider_q = (
        select(DownloadJobDB.provider, func.count(DownloadJobDB.id))
        .where(DownloadJobDB.provider.isnot(None))
        .where(DownloadJobDB.provider != "")
        .group_by(DownloadJobDB.provider)
    )
    if tenant_id and tenant_id != "default":
        provider_q = provider_q.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        provider_q = provider_q.where(DownloadJobDB.user_id == user_id)
    provider_result = await session.execute(provider_q)
    by_provider = dict(provider_result.all())

    type_q = (
        select(DownloadJobDB.media_type, func.count(DownloadJobDB.id)).group_by(
            DownloadJobDB.media_type
        )
    )
    if tenant_id and tenant_id != "default":
        type_q = type_q.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        type_q = type_q.where(DownloadJobDB.user_id == user_id)
    type_result = await session.execute(type_q)
    by_type = dict(type_result.all())

    bytes_q = select(
        func.coalesce(func.sum(DownloadJobDB.file_size_bytes), 0)
    )
    if tenant_id and tenant_id != "default":
        bytes_q = bytes_q.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        bytes_q = bytes_q.where(DownloadJobDB.user_id == user_id)
    bytes_result = await session.execute(bytes_q)
    total_bytes = bytes_result.scalar() or 0

    avg_q = select(
        func.coalesce(func.avg(DownloadJobDB.file_size_bytes), 0)
    ).where(DownloadJobDB.file_size_bytes.isnot(None))
    if tenant_id and tenant_id != "default":
        avg_q = avg_q.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        avg_q = avg_q.where(DownloadJobDB.user_id == user_id)
    avg_result = await session.execute(avg_q)
    avg_bytes = int(avg_result.scalar() or 0)

    most_provider = (
        max(by_provider, key=by_provider.get) if by_provider else None
    )

    quality_q = (
        select(DownloadJobDB.quality, func.count(DownloadJobDB.id))
        .where(DownloadJobDB.quality.isnot(None))
        .group_by(DownloadJobDB.quality)
        .order_by(func.count(DownloadJobDB.id).desc())
        .limit(1)
    )
    if tenant_id and tenant_id != "default":
        quality_q = quality_q.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        quality_q = quality_q.where(DownloadJobDB.user_id == user_id)
    quality_result = await session.execute(quality_q)
    quality_row = quality_result.first()
    most_quality = quality_row[0] if quality_row else None

    return {
        "total_downloads": total,
        "completed": by_status.get("completed", 0),
        "failed": by_status.get("failed", 0),
        "cancelled": by_status.get("cancelled", 0),
        "total_bytes_downloaded": total_bytes,
        "total_bytes_human": format_size(total_bytes),
        "avg_file_size_bytes": avg_bytes,
        "avg_file_size_human": format_size(avg_bytes),
        "most_used_provider": most_provider,
        "most_used_quality": most_quality,
        "downloads_by_provider": by_provider,
        "downloads_by_status": by_status,
        "downloads_by_media_type": by_type,
    }


async def clear_history(
    session: AsyncSession,
    tenant_id: str | None = None,
    user_id: str | None = None,
    status: str | None = None,
) -> int:
    """Delete history entries.

    Only deletes terminal jobs (completed/failed/cancelled).
    Never deletes active jobs.
    """
    terminal = ["completed", "failed", "cancelled"]

    if status and status in terminal:
        query = delete(DownloadJobDB).where(DownloadJobDB.status == status)
    else:
        query = delete(DownloadJobDB).where(
            DownloadJobDB.status.in_(terminal)
        )

    if tenant_id and tenant_id != "default":
        query = query.where(DownloadJobDB.tenant_id == tenant_id)
    if user_id and user_id != "system":
        query = query.where(DownloadJobDB.user_id == user_id)

    result = await session.execute(query)
    return result.rowcount or 0
