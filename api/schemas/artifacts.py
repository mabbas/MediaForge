"""Pydantic schemas for Media Artifacts & Retention (STEP 02-E1 / STEP 02-E2A)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class MediaArtifactResponse(BaseModel):
    """Response schema for a single media artifact."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    user_id: Optional[str] = None
    api_key_id: Optional[str] = None
    artifact_type: str = Field(..., description="'clip' or 'merge'")
    source_download_id: Optional[str] = None
    parent_artifact_id: Optional[str] = None
    storage_path: str = Field(..., description="Safe relative storage path within tenant media root")
    filename: str
    format: str
    duration_seconds: Optional[float] = 0.0
    file_size_bytes: int = 0
    file_size_human: str = "0 B"
    sha256_checksum: Optional[str] = None
    status: str = Field(..., description="'pending', 'processing', 'ready', 'failed', 'deleted'")
    error_message: Optional[str] = None
    input_sources: list[Any] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None


class ArtifactListResponse(BaseModel):
    """Response schema for paginated artifact list."""

    items: list[MediaArtifactResponse]
    total: int
    limit: int
    offset: int


class RetentionItemResponse(BaseModel):
    """Details for a single artifact in retention preview."""
    model_config = ConfigDict(from_attributes=True)

    artifact_id: Optional[str] = None
    tenant_id: str
    user_id: Optional[str] = None
    storage_path: str
    file_size_bytes: int = 0
    created_at: Optional[str] = None
    status: Optional[str] = None
    classification: str
    action: str
    reason: str
    is_customer_media: bool
    is_protected: bool
    eligible_for_cleanup: bool


class RetentionPreviewResponse(BaseModel):
    """Summary of dry-run retention audit."""
    model_config = ConfigDict(from_attributes=True)

    timestamp: str
    storage_root: str
    policy: str
    default_retention_days: int
    effective_retention_days: int
    filter_tenant_id: Optional[str] = None
    total_scanned_artifacts: int
    total_scanned_files: int
    customer_media_retained_count: int
    download_job_protected_count: int
    referenced_source_protected_count: int
    historical_anomalies_count: int
    already_deleted_count: int
    eligible_for_cleanup_count: int
    estimated_reclaimable_bytes: int
    tenant_summaries: dict[str, Any] = Field(default_factory=dict)
    items: list[RetentionItemResponse] = Field(default_factory=list)
