"""Tests for API schemas."""

from __future__ import annotations

import pytest

from api.schemas.common import (
    APIResponse,
    DiskUsageResponse,
    ErrorResponse,
    HealthResponse,
    PaginatedResponse,
    ReadyResponse,
)


def test_api_response():
    r = APIResponse()
    assert r.success is True


def test_error_response():
    r = ErrorResponse(error="fail", error_code="TEST")
    assert r.success is False


def test_health_response():
    r = HealthResponse(version="0.1.0", uptime_seconds=100)
    assert r.status == "healthy"


def test_ready_response():
    r = ReadyResponse(
        status="healthy",
        version="0.1.0",
        uptime_seconds=100,
        database="ok",
        ffmpeg="ok",
        yt_dlp="2024.12.01",
        disk_free_gb=45.2,
        providers_count=2,
    )
    assert r.database == "ok"


def test_paginated_response():
    r = PaginatedResponse()
    assert r.page_size == 20


def test_disk_usage_response():
    r = DiskUsageResponse(
        total_gb=500,
        used_gb=200,
        free_gb=300,
        usage_percent=40,
        download_dir="/dl",
        min_space_mb=500,
    )
    assert r.free_gb == 300
