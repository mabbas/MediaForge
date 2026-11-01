"""Usage tracking and commercial metering schemas (STEP 02-E2B)."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class UsageSummaryResponse(BaseModel):
    """Usage summary for current user (backwards-compatible)."""

    success: bool = True
    user_id: str
    tenant_id: str
    tier: str
    today: dict[str, int] = Field(description="Today's usage counters")
    limits: dict[str, dict] = Field(description="Limits with remaining quota")


class UsageHistoryResponse(BaseModel):
    """Usage history over time."""

    success: bool = True
    user_id: str
    days: list[dict]
    total_downloads: int = 0
    total_bytes: int = 0


class UsageEventCreate(BaseModel):
    """Payload to record a metered usage event."""

    event_type: str = Field(..., description="Type of event: download, clip, merge, transcription")
    idempotency_key: str = Field(..., max_length=128, description="Unique event idempotency key")
    resource_id: str | None = Field(None, max_length=64, description="ID of artifact, job, or transcript")
    quantity: float = Field(1.0, description="Measurement quantity")
    unit: str = Field("count", max_length=32, description="Measurement unit (count, bytes, seconds, minutes)")
    bytes: int = Field(0, description="Payload or file bytes")
    duration_seconds: float = Field(0.0, description="Duration in seconds")
    metadata_json: dict[str, Any] = Field(default_factory=dict, description="Arbitrary event metadata")


class UsageEventResponse(BaseModel):
    """Metered usage event record."""

    id: str
    tenant_id: str
    user_id: str | None = None
    api_key_id: str | None = None
    event_type: str
    idempotency_key: str
    resource_id: str | None = None
    quantity: float
    unit: str
    bytes: int
    duration_seconds: float
    auth_method: str
    client_ip: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str

    model_config = {"from_attributes": True}


class UsageEventListResponse(BaseModel):
    """Paginated list of usage events."""

    success: bool = True
    tenant_id: str
    total: int
    page: int
    page_size: int
    events: list[UsageEventResponse]


class QuotaItem(BaseModel):
    """Advisory quota evaluation for a single metric."""

    metric: str
    period: str
    used: float
    limit: int = -1  # -1 represents UNLIMITED
    remaining: int = -1  # -1 represents UNLIMITED
    unit: str
    is_exceeded: bool = False
    is_hard_limit: bool = False


class TenantQuotasResponse(BaseModel):
    """Tenant-scoped advisory quota report."""

    success: bool = True
    tenant_id: str
    tier: str
    period: str
    quotas: dict[str, QuotaItem]


class CommercialUsageSummaryResponse(BaseModel):
    """Aggregated commercial usage report for a tenant."""

    success: bool = True
    tenant_id: str
    user_id: str | None = None
    period: str
    start_date: str | None = None
    end_date: str | None = None
    totals: dict[str, Any]
    breakdown_by_type: dict[str, Any]
    breakdown_by_user: dict[str, Any]
    breakdown_by_api_key: dict[str, Any]
