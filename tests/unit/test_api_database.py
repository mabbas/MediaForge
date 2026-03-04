"""Tests for database models."""

from __future__ import annotations

import pytest

from api.database.models import (
    APIKeyDB,
    AppSettingDB,
    DownloadJobDB,
    TenantDB,
    TranscriptDB,
    UsageTrackingDB,
    UserDB,
)


def test_tenant_model():
    cols = [c.name for c in TenantDB.__table__.columns]
    assert "id" in cols
    assert "slug" in cols
    assert "tier" in cols
    assert "is_active" in cols


def test_user_model():
    cols = [c.name for c in UserDB.__table__.columns]
    assert "id" in cols
    assert "tenant_id" in cols
    assert "email" in cols
    assert "password_hash" in cols
    assert "role" in cols
    assert "tier_override" in cols


def test_api_key_model():
    cols = [c.name for c in APIKeyDB.__table__.columns]
    assert "id" in cols
    assert "key_hash" in cols
    assert "permissions" in cols
    assert "rate_limit" in cols


def test_download_job_model():
    cols = [c.name for c in DownloadJobDB.__table__.columns]
    assert "id" in cols
    assert "url" in cols
    assert "status" in cols
    assert "user_id" in cols
    assert "tenant_id" in cols
    assert "parent_job_id" in cols
    assert "metadata_json" in cols
    assert "bytes_downloaded" in cols
    assert "total_bytes" in cols


def test_transcript_model():
    cols = [c.name for c in TranscriptDB.__table__.columns]
    assert "url" in cols
    assert "language" in cols
    assert "word_count" in cols


def test_usage_tracking_model():
    cols = [c.name for c in UsageTrackingDB.__table__.columns]
    assert "user_id" in cols
    assert "downloads_count" in cols


def test_app_settings_model():
    cols = [c.name for c in AppSettingDB.__table__.columns]
    assert "key" in cols
    assert "value" in cols


def test_models_bind_to_base_metadata():
    """All models are attached to the shared Base metadata."""
    models = [
        TenantDB,
        UserDB,
        APIKeyDB,
        DownloadJobDB,
        TranscriptDB,
        UsageTrackingDB,
        AppSettingDB,
    ]
    for model in models:
        assert model.__table__.metadata is TenantDB.__table__.metadata
