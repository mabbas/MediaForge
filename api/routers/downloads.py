"""Download management endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.dependencies import CurrentUser, get_app, get_current_user
from api.schemas.common import ErrorResponse
from api.schemas.downloads import DownloadJobResponse, DownloadSubmitResponse
from api.services.download_service import (
    get_job_from_db,
    job_to_response,
    persist_job,
)
from src.grabitdown import GrabItDown

router = APIRouter(prefix="/downloads", tags=["Downloads"])


class SubmitDownloadRequest(BaseModel):
    """Single download submit body."""

    url: str
    mode: str = "video"
    quality: str = "1080p"
    priority: str = "normal"
    start: bool = False  # If False, job is added to queue but not started (deferred)


class BatchDownloadRequest(BaseModel):
    """Batch download submit body."""

    urls: list[str]
    mode: str = "video"
    quality: str = "1080p"
    start: bool = False


class PlaylistDownloadRequest(BaseModel):
    """Playlist download submit body."""

    url: str
    mode: str = "video"
    quality: str = "1080p"
    concurrency: int = 2
    start: bool = False


@router.post(
    "",
    summary="Submit a download",
    description="Queue a single video or audio download.",
    response_model=DownloadSubmitResponse,
    status_code=202,
)
async def submit_download(
    body: SubmitDownloadRequest,
    session: AsyncSession = Depends(get_session),
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Submit a single download."""
    job = app.download(
        url=body.url,
        mode=body.mode,
        quality=body.quality,
        priority=body.priority,
        user_id=user.user_id,
        tier=user.tier,
        start=body.start,
    )
    await persist_job(session, job, user_id=user.user_id, tenant_id=user.tenant_id)
    await session.commit()
    job_resp = job_to_response(job)
    return DownloadSubmitResponse(
        success=True,
        message="Download queued",
        job=DownloadJobResponse(**job_resp),
    )


@router.post(
    "/batch",
    summary="Submit batch downloads",
    description="Queue multiple URLs for download.",
    status_code=202,
)
async def submit_batch(
    body: BatchDownloadRequest,
    session: AsyncSession = Depends(get_session),
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Submit a batch of downloads."""
    jobs = app.download_batch(
        urls=body.urls,
        mode=body.mode,
        quality=body.quality,
        user_id=user.user_id,
        tier=user.tier,
        start=body.start,
    )
    for job in jobs:
        await persist_job(session, job, user_id=user.user_id, tenant_id=user.tenant_id)
    await session.commit()
    return {
        "jobs": [job_to_response(j) for j in jobs],
        "total": len(jobs),
    }


@router.post(
    "/playlist",
    summary="Submit playlist download",
    description="Queue all items from a playlist URL.",
    status_code=202,
)
async def submit_playlist(
    body: PlaylistDownloadRequest,
    session: AsyncSession = Depends(get_session),
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Submit a playlist for download."""
    jobs = app.download_playlist(
        url=body.url,
        mode=body.mode,
        quality=body.quality,
        concurrency=body.concurrency,
        user_id=user.user_id,
        tier=user.tier,
        start=body.start,
    )
    for job in jobs:
        await persist_job(session, job, user_id=user.user_id, tenant_id=user.tenant_id)
    await session.commit()
    return {
        "jobs": [job_to_response(j) for j in jobs],
        "total_items": len(jobs),
    }


@router.post(
    "/{job_id}/start",
    summary="Start a deferred download",
    description="Move a deferred job to the queue so it starts according to concurrency.",
    status_code=200,
)
async def start_download(
    job_id: str,
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Start a deferred download job."""
    ok = app.start_download_job(job_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Job not found or not deferred (already queued/active/finished)",
        )
    return {"success": True, "message": "Download started"}


@router.post(
    "/{job_id}/pause",
    summary="Pause a download",
    description="Pause a single download (queued, deferred, or active). It can be resumed later.",
    status_code=200,
)
async def pause_download(
    job_id: str,
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Pause a download job."""
    ok = app.pause_download_job(job_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Job not found or not pausable (already completed/cancelled)",
        )
    return {"success": True, "message": "Download paused"}


@router.post(
    "/{job_id}/cancel",
    summary="Cancel a download",
    status_code=200,
)
async def cancel_download(
    job_id: str,
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Cancel a queued, deferred, or active download."""
    ok = app.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    return {"success": True, "message": "Cancelled"}


@router.post(
    "/{job_id}/move-up",
    summary="Move download up",
    description="Move a queued job one position up in the download list.",
    status_code=200,
)
async def move_download_up(
    job_id: str,
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Move a queued job up in priority order."""
    ok = app.move_job_up(job_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Job not found in queue or already at top",
        )
    return {"success": True, "message": "Moved up"}


@router.post(
    "/{job_id}/move-down",
    summary="Move download down",
    description="Move a queued job one position down in the download list.",
    status_code=200,
)
async def move_download_down(
    job_id: str,
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Move a queued job down in priority order."""
    ok = app.move_job_down(job_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Job not found in queue or already at bottom",
        )
    return {"success": True, "message": "Moved down"}


@router.get(
    "",
    summary="List downloads",
    description="List active and recent downloads.",
)
async def list_downloads(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """List jobs from engine."""
    jobs = app.get_all_jobs()
    return {
        "success": True,
        "jobs": [job_to_response(j) for j in jobs],
        "total": len(jobs),
    }


@router.post(
    "/{job_id}/resume",
    response_model=DownloadSubmitResponse,
    status_code=202,
    summary="Resume a paused/interrupted download",
    description="Resume a download that was paused or interrupted. Creates a new job with the same parameters.",
    responses={
        202: {"description": "Download resumed"},
        404: {"model": ErrorResponse},
        400: {"model": ErrorResponse},
    },
)
async def resume_download(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Resume an interrupted or failed download. Re-queues the same job when possible; otherwise creates a new job from DB/engine."""
    if app.requeue_job(job_id):
        same_job = app.get_job(job_id)
        if same_job:
            job_resp = job_to_response(same_job)
            return DownloadSubmitResponse(
                message="Download resumed (same job)",
                job=DownloadJobResponse(**job_resp),
            )

    resumable_statuses = ("paused", "interrupted", "failed")
    url = None
    mode = "video"
    quality = "1080p"
    priority = "normal"

    db_job = await get_job_from_db(session, job_id, user.tenant_id)
    if db_job and db_job.status in resumable_statuses:
        url = db_job.url
        mode = db_job.media_type or "video"
        quality = db_job.quality or "1080p"
        priority = db_job.priority or "normal"

    if not url:
        engine_job = app.get_job(job_id)
        if engine_job and engine_job.request and engine_job.progress:
            status_val = (
                engine_job.progress.status.value
                if hasattr(engine_job.progress.status, "value")
                else str(engine_job.progress.status)
            )
            if status_val in resumable_statuses:
                url = engine_job.request.url
                mode = getattr(
                    engine_job.request.media_type, "value", "video"
                ) if hasattr(engine_job.request, "media_type") else "video"
                quality = getattr(
                    engine_job.request.quality, "value", "1080p"
                ) if hasattr(engine_job.request, "quality") else "1080p"
                priority = getattr(engine_job, "priority", "normal") or "normal"

    if not url:
        if db_job:
            raise HTTPException(
                status_code=400,
                detail=f"Job status is {db_job.status}, cannot resume (only paused/interrupted/failed can be resumed)",
            )
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    new_job = app.download(
        url=url,
        mode=mode,
        quality=quality,
        priority=priority,
        user_id=user.user_id,
        tier=user.tier,
        start=True,
    )

    await persist_job(session, new_job, user_id=user.user_id, tenant_id=user.tenant_id)
    await session.commit()

    job_resp = job_to_response(new_job)
    return DownloadSubmitResponse(
        message="Download resumed",
        job=DownloadJobResponse(**job_resp),
    )


@router.get(
    "/{job_id}/file",
    summary="Download completed file",
    description="Stream/download the completed file for a finished download job.",
    responses={
        200: {"description": "File stream", "content": {"application/octet-stream": {}}},
        404: {"model": ErrorResponse},
        400: {"model": ErrorResponse},
    },
)
async def download_file(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Serve a completed download file."""
    file_path = None
    title = None

    job = app.get_job(job_id)
    if job and job.result and job.result.file_path:
        file_path = job.result.file_path
        title = getattr(job.result, "title", None)
    else:
        db_job = await get_job_from_db(session, job_id, user.tenant_id)
        if not db_job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if db_job.status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Job not completed (status: {db_job.status})",
            )
        file_path = db_job.file_path
        title = db_job.title

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    filename = os.path.basename(file_path)
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream",
    )
