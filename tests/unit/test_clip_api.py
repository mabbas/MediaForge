"""Tests for clip API endpoints."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from api.schemas.clips import (
    ClipExtractRequest,
    ClipResultResponse,
    ClipValidateRequest,
    ClipValidateResponse,
)


def test_clip_extract_request_schema():
    req = ClipExtractRequest(
        source="https://youtube.com/watch?v=abc",
        start_time="00:01:00",
        end_time="00:02:30",
    )
    assert req.mode == "precise"
    assert req.output_format == "mp4"
    assert req.audio_bitrate == "192k"


def test_clip_extract_request_minimal():
    req = ClipExtractRequest(
        source="/path/video.mp4",
        start_time="10",
        end_time="30",
    )
    assert req.source == "/path/video.mp4"


def test_clip_validate_request():
    req = ClipValidateRequest(
        start_time="1:00",
        end_time="2:30",
    )
    assert req.source is None


def test_clip_result_response():
    res = ClipResultResponse(
        success=True,
        clip_id="abc123",
        source="/path/video.mp4",
        output_path="/path/clip.mp4",
        start_time="1:00",
        end_time="2:30",
        duration_seconds=90.0,
        file_size_bytes=5242880,
        file_size_human="5.00 MB",
    )
    assert res.success is True
    assert res.duration_seconds == 90.0


def test_clip_validate_response():
    res = ClipValidateResponse(
        valid=True,
        start_seconds=60.0,
        end_seconds=150.0,
        duration_seconds=90.0,
    )
    assert res.valid is True


def test_clip_endpoints_in_openapi():
    """Verify clip endpoints are registered."""
    os.environ.setdefault(
        "GID_API_DATABASE_URL",
        "sqlite+aiosqlite:///./data/test_clip.db",
    )

    from api.main import app

    client = TestClient(app)
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]

    clip_paths = [p for p in paths if "/clips" in p]
    assert len(clip_paths) >= 2, f"Expected clip endpoints, found: {clip_paths}"
