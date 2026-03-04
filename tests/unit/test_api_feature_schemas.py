"""Tests for feature/usage/history schemas."""

from __future__ import annotations

import pytest

from api.schemas.features import (
    FeaturesResponse,
    TierComparisonResponse,
    TierInfoSchema,
    TierLimitsResponse,
)
from api.schemas.usage import UsageSummaryResponse
from api.schemas.history import (
    ClearHistoryResponse,
    HistoryEntrySchema,
    HistoryResponse,
    HistoryStatsResponse,
)
from api.schemas.config import ConfigResponse, ConfigUpdateRequest


def test_features_response():
    r = FeaturesResponse(
        mode="personal",
        current_tier="platinum",
        features={"video_download": True},
    )
    assert r.current_tier == "platinum"


def test_tier_comparison():
    r = TierComparisonResponse(
        tiers=[
            TierInfoSchema(
                name="basic",
                display_name="Basic",
                price_monthly=0,
                features=[],
            )
        ],
        current_tier="basic",
    )
    assert len(r.tiers) == 1


def test_tier_limits():
    r = TierLimitsResponse(
        tier="pro",
        limits={"video": {"max_quality": "1080p"}},
    )
    assert r.tier == "pro"


def test_usage_summary():
    r = UsageSummaryResponse(
        user_id="alice",
        tenant_id="default",
        tier="pro",
        today={"daily_downloads": 5},
        limits={},
    )
    assert r.tier == "pro"


def test_history_entry():
    e = HistoryEntrySchema(
        job_id="test",
        url="https://test.com",
        media_type="video",
        status="completed",
        title="Test Video",
    )
    assert e.status == "completed"


def test_history_response():
    r = HistoryResponse(
        entries=[],
        total=0,
        page=1,
        page_size=20,
        total_pages=0,
    )
    assert r.total == 0


def test_history_stats():
    r = HistoryStatsResponse(
        total_downloads=100,
        completed=80,
        failed=15,
        cancelled=5,
    )
    assert r.total_downloads == 100


def test_config_response():
    r = ConfigResponse(
        app={"name": "GrabItDown"},
        download={},
        video={},
        audio={},
        transcript={},
        resume={},
        providers={},
    )
    assert r.app["name"] == "GrabItDown"


def test_config_update_request():
    r = ConfigUpdateRequest(
        max_concurrent_downloads=5,
        bandwidth_limit_bps=10485760,
    )
    assert r.max_concurrent_downloads == 5


def test_clear_history_response():
    r = ClearHistoryResponse(
        message="Cleared 10 entries",
        deleted_count=10,
    )
    assert r.deleted_count == 10
