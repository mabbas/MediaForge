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


class BatchDownloadRequest(BaseModel):
    """Batch download submit body."""

    urls: list[str]
    mode: str = "video"
    quality: str = "1080p"


class PlaylistDownloadRequest(BaseModel):
    """Playlist download submit body."""

    url: str
    mode: str = "video"
    quality: str = "1080p"
    concurrency: int = 2


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
    )
    for job in jobs:
        await persist_job(session, job, user_id=user.user_id, tenant_id=user.tenant_id)
    await session.commit()
    return {
        "jobs": [job_to_response(j) for j in jobs],
        "total_items": len(jobs),
    }


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
    """Cancel a queued or active download."""
    ok = app.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    return {"success": True, "message": "Cancelled"}


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
    """Resume an interrupted download."""
    db_job = await get_job_from_db(session, job_id, user.tenant_id)

    if not db_job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if db_job.status not in ("paused", "interrupted", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Job status is {db_job.status}, cannot resume",
        )

    new_job = app.download(
        url=db_job.url,
        mode=db_job.media_type,
        quality=db_job.quality or "1080p",
        priority=db_job.priority,
        user_id=user.user_id,
        tier=user.tier,
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
