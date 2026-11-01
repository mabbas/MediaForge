"""Asynchronous background media jobs API router (STEP 03-B)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.dependencies import CurrentUser, get_current_user, require_scope
from api.schemas.clips import ClipExtractRequest
from api.schemas.jobs import (
    JobCancelResponse,
    JobDetailResponse,
    JobListResponse,
    JobSubmitResponse,
)
from api.schemas.merge import MergeRequest as MergeRequestSchema
from api.services.job_service import (
    get_authorized_job,
    list_authorized_jobs,
    request_job_cancellation,
    submit_clip_job,
    submit_merge_job,
    work_item_to_detail_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
)


@router.post(
    "/clips",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit asynchronous clip extraction job",
    description="Enqueue an offline clip extraction job for asynchronous worker processing.",
)
async def create_clip_job(
    request_data: ClipExtractRequest,
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    user: CurrentUser = Depends(require_scope("clips:write")),
    db: AsyncSession = Depends(get_session),
):
    """Enqueue offline clip extraction work item."""
    job = await submit_clip_job(
        db=db,
        user=user,
        request=request_data,
        idempotency_key=x_idempotency_key,
    )

    return JobSubmitResponse(
        job_id=job.id,
        status=job.status,
        operation_type=job.operation_type,
        created_at=job.created_at,
        status_url=f"/api/v1/jobs/{job.id}",
    )


@router.post(
    "/merges",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit asynchronous video merge job",
    description="Enqueue a video merge job for asynchronous worker processing.",
)
async def create_merge_job(
    request_data: MergeRequestSchema,
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    user: CurrentUser = Depends(require_scope("clips:write")),
    db: AsyncSession = Depends(get_session),
):
    """Enqueue video merge work item."""
    job = await submit_merge_job(
        db=db,
        user=user,
        clips=request_data.clips,
        output_format=request_data.output_format,
        mode=request_data.mode,
        idempotency_key=x_idempotency_key,
    )

    return JobSubmitResponse(
        job_id=job.id,
        status=job.status,
        operation_type=job.operation_type,
        created_at=job.created_at,
        status_url=f"/api/v1/jobs/{job.id}",
    )


@router.get(
    "",
    response_model=JobListResponse,
    summary="List background media jobs",
    description="List background media jobs for the authenticated tenant with intra-tenant scoping.",
)
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status (queued, running, succeeded, failed, cancelled)"),
    operation_type: Optional[str] = Query(None, description="Filter by operation (clip_extract, merge)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """List jobs with tenant scoping and intra-tenant member isolation."""
    items, total = await list_authorized_jobs(
        db=db,
        user=user,
        status=status,
        operation_type=operation_type,
        page=page,
        page_size=page_size,
    )

    job_responses = [work_item_to_detail_response(item, user=user) for item in items]
    return JobListResponse(
        success=True,
        tenant_id=user.tenant_id,
        total=total,
        page=page,
        page_size=page_size,
        jobs=job_responses,
    )


@router.get(
    "/{job_id}",
    response_model=JobDetailResponse,
    summary="Get background job status and result",
    description="Query detailed status, progress, and result artifact reference for a background job.",
)
async def get_job_detail(
    job_id: str,
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """Retrieve single job detail with anti-enumeration protection."""
    item = await get_authorized_job(db, job_id, user)
    return work_item_to_detail_response(item, user=user)


@router.post(
    "/{job_id}/cancel",
    response_model=JobCancelResponse,
    summary="Cancel a queued or running background job",
    description="Cancel a background media job. If queued, cancels immediately; if running, signals worker to abort.",
)
async def cancel_job(
    job_id: str,
    user: CurrentUser = Depends(require_scope("clips:write")),
    db: AsyncSession = Depends(get_session),
):
    """Request job cancellation."""
    item, msg = await request_job_cancellation(db, job_id, user)
    return JobCancelResponse(
        success=True,
        job_id=item.id,
        status=item.status,
        message=msg,
    )
