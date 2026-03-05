"""Tests for merge API schemas and endpoints."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from api.schemas.merge import (
    MergeRequest,
    MergeResultResponse,
)


def test_merge_request_schema():
    req = MergeRequest(
        clips=["/a.mp4", "/b.mp4"],
    )
    assert req.mode == "auto"
    assert req.output_format == "mp4"
    assert len(req.clips) == 2


def test_merge_request_three_clips():
    req = MergeRequest(
        clips=["/a.mp4", "/b.mp4", "/c.mp4"],
        mode="reencode",
        resolution="1280x720",
    )
    assert len(req.clips) == 3
    assert req.resolution == "1280x720"


def test_merge_result_response():
    res = MergeResultResponse(
        success=True,
        merge_id="abc123",
        clip_count=3,
        output_path="/merged.mp4",
        total_duration_seconds=30.5,
        file_size_bytes=10485760,
        file_size_human="10.00 MB",
        clips_used=["/a.mp4", "/b.mp4", "/c.mp4"],
    )
    assert res.success is True
    assert res.clip_count == 3


def test_merge_endpoints_in_openapi():
    os.environ.setdefault(
        "GID_API_DATABASE_URL",
        "sqlite+aiosqlite:///./data/test_merge.db",
    )

    from api.main import app

    client = TestClient(app)
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]

    merge_paths = [p for p in paths if "merge" in p]
    assert len(merge_paths) >= 1, f"Expected merge endpoints, found: {merge_paths}"
