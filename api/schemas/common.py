"""Common API response schemas."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Standard API response wrapper."""

    success: bool = True
    data: T | None = None
    message: str | None = None

    model_config = {"json_schema_extra": {"examples": [{"success": True, "data": None}]}}


class ErrorResponse(BaseModel):
    """Error response."""

    success: bool = False
    error: str
    error_code: str | None = None
    details: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    """Liveness health check response."""

    status: str = "healthy"
    version: str
    uptime_seconds: float


class ReadyResponse(BaseModel):
    """Readiness check response with dependency status."""

    status: str  # healthy | degraded | unhealthy
    version: str
    uptime_seconds: float
    database: str  # ok | error: ...
    ffmpeg: str  # ok | not found
    yt_dlp: str  # version string | not installed
    disk_free_gb: float
    providers_count: int

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "healthy",
                    "version": "0.1.0",
                    "uptime_seconds": 3600.5,
                    "database": "ok",
                    "ffmpeg": "ok",
                    "yt_dlp": "2024.12.01",
                    "disk_free_gb": 45.2,
                    "providers_count": 2,
                }
            ]
        }
    }


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list response."""

    items: list[T] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    has_more: bool = False


class DiskUsageResponse(BaseModel):
    """Disk usage for download directory."""

    total_gb: float
    used_gb: float
    free_gb: float
    usage_percent: float
    download_dir: str
    min_space_mb: int
