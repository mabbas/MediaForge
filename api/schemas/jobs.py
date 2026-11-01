"""API schemas for background media jobs (STEP 03-B)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from api.schemas.artifacts import MediaArtifactResponse


class JobSubmitResponse(BaseModel):
    """Response returned upon asynchronous job submission (HTTP 202 Accepted)."""

    job_id: str = Field(..., description="Unique opaque job identifier")
    status: str = Field("queued", description="Initial job status")
    operation_type: str = Field(..., description="Operation type: clip_extract or merge")
    created_at: datetime = Field(..., description="Timestamp when job was created (UTC)")
    status_url: str = Field(..., description="Relative URL to query job status")
    correlation_id: Optional[str] = Field(None, description="Optional request correlation ID")


class JobDetailResponse(BaseModel):
    """Detailed status and outcome of a persistent media work item."""

    job_id: str
    tenant_id: str
    operation_type: str
    status: str  # queued, running, retry_wait, succeeded, failed, cancelled
    progress_percent: float = 0.0
    attempt_count: int = 0
    max_attempts: int = 3
    claimed_by: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    available_at: Optional[datetime] = None
    cancellation_requested: bool = False
    result_artifact_id: Optional[str] = None
    result_artifact: Optional[MediaArtifactResponse] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime


class JobListResponse(BaseModel):
    """Paginated list of tenant background jobs."""

    success: bool = True
    tenant_id: str
    total: int
    page: int
    page_size: int
    jobs: list[JobDetailResponse]


class JobCancelResponse(BaseModel):
    """Response returned upon requesting job cancellation."""

    success: bool
    job_id: str
    status: str
    message: str
