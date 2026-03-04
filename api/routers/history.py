"""Download history endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.dependencies import CurrentUser, get_current_user
from api.schemas.history import (
    ClearHistoryResponse,
    HistoryEntrySchema,
    HistoryResponse,
    HistoryStatsResponse,
)
from api.services.history_service import (
    clear_history,
    get_history,
    get_history_stats,
)
from src.cli.console import format_size

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/history", tags=["History"])


@router.get(
    "",
    response_model=HistoryResponse,
    summary="Download history",
    description="Get paginated download history with optional filtering.",
)
async def list_history(
    status: str
    | None = Query(
        None,
        description="Filter: completed/failed/cancelled",
    ),
    media_type: str
    | None = Query(None, description="Filter: video/audio"),
    provider: str | None = Query(None, description="Filter by provider name"),
    search: str | None = Query(None, description="Search in title and URL"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """Get download history."""
    jobs, total = await get_history(
        session,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        status=status,
        media_type=media_type,
        provider=provider,
        search=search,
        page=page,
        page_size=page_size,
    )
    entries = [
        HistoryEntrySchema(
            job_id=j.id,
            url=j.url,
            provider=j.provider,
            media_type=j.media_type,
            status=j.status,
            title=j.title,
            quality=j.quality,
            file_path=j.file_path,
            file_size_bytes=j.file_size_bytes,
            file_size_human=format_size(j.file_size_bytes),
            duration_seconds=j.duration_seconds,
            error_message=j.error_message,
            user_id=j.user_id,
            created_at=j.created_at,
            completed_at=j.completed_at,
        )
        for j in jobs
    ]
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return HistoryResponse(
        entries=entries,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/stats",
    response_model=HistoryStatsResponse,
    summary="History statistics",
    description="Get aggregated download statistics.",
)
async def history_stats(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """Get download history statistics."""
    stats = await get_history_stats(
        session,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )
    return HistoryStatsResponse(**stats)


@router.delete(
    "",
    response_model=ClearHistoryResponse,
    summary="Clear history",
    description="Delete completed/failed/cancelled download history. Active jobs are never deleted.",
)
async def delete_history(
    status: str
    | None = Query(
        None,
        description="Only delete this status (completed/failed/cancelled)",
    ),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """Clear download history."""
    count = await clear_history(
        session,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        status=status,
    )
    label = f"{status} " if status else ""
    return ClearHistoryResponse(
        message=f"Cleared {count} {label}history entries",
        deleted_count=count,
    )
