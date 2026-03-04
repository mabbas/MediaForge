"""Tests for feature/usage/history/config endpoints."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


class _FakeGrabItDown:
    """Minimal fake so FastAPI does not inspect MagicMock (*args/**kwargs)."""
    _engine = None
    _usage = None

    def __init__(self):
        self._engine = MagicMock()
        self._usage = MagicMock()
        self._usage.get_all_usage.return_value = {}
        self._usage.get_usage.return_value = 0
        self._usage.get_remaining.return_value = -1
        self._usage.reset = MagicMock()
        self.get_stats = MagicMock(return_value={
            "active": 0, "max_concurrent": 3,
            "queue": {"total": 0, "high": 0, "normal": 0, "low": 0},
            "total_jobs": 0, "is_paused": False, "jobs_by_status": {},
        })
        self.list_providers = MagicMock(return_value=[])
        self.get_all_jobs = MagicMock(return_value=[])

    def set_bandwidth_limit(self, bytes_per_second: int) -> None:
        pass


@pytest.fixture
def client(tmp_path):
    """Test client with SQLite DB and mocked GrabItDown app for usage/config."""
    db_path = (tmp_path / "test.db").as_posix()
    os.environ["GID_API_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["GID_API_SKIP_SEED"] = "1"
    from api.config import get_api_settings
    get_api_settings.cache_clear()

    import api.dependencies as deps

    deps._app_instance = None
    mock_app = _FakeGrabItDown()

    from api.main import app
    from api.database.connection import get_session
    from api.dependencies import get_app

    async def _mock_get_session() -> AsyncGenerator[AsyncSession, None]:
        session = MagicMock(spec=AsyncSession)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
        result.scalar.return_value = 0
        result.first.return_value = None
        result.rowcount = 0
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.close = AsyncMock()
        yield session

    app.dependency_overrides[get_session] = _mock_get_session
    app.dependency_overrides[get_app] = lambda: mock_app
    try:
        with TestClient(app) as c:
            yield c, mock_app
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_app, None)


def test_get_features(client):
    c, mock = client
    r = c.get("/api/v1/features")
    assert r.status_code == 200
    data = r.json()
    assert "mode" in data
    assert "features" in data
    assert "current_tier" in data


def test_compare_tiers(client):
    c, mock = client
    r = c.get("/api/v1/features/tiers")
    assert r.status_code == 200
    data = r.json()
    assert "tiers" in data
    assert len(data["tiers"]) >= 1


def test_tier_limits(client):
    c, mock = client
    r = c.get("/api/v1/features/tiers/basic/limits")
    assert r.status_code == 200
    data = r.json()
    assert data["tier"] == "basic"
    assert "video_download" in data["limits"]


def test_tier_limits_not_found(client):
    c, mock = client
    r = c.get("/api/v1/features/tiers/nonexistent/limits")
    assert r.status_code == 404


def test_get_usage(client):
    c, mock = client
    r = c.get("/api/v1/usage")
    assert r.status_code == 200
    data = r.json()
    assert "user_id" in data
    assert "tier" in data
    assert "limits" in data


def test_reset_usage(client):
    c, mock = client
    r = c.post("/api/v1/usage/reset")
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_get_history(client):
    c, mock = client
    r = c.get("/api/v1/history")
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data
    assert "total" in data
    assert "page" in data


def test_get_history_with_filters(client):
    c, mock = client
    r = c.get(
        "/api/v1/history?status=completed&media_type=video&page=1&page_size=10"
    )
    assert r.status_code == 200


def test_get_history_stats(client):
    c, mock = client
    r = c.get("/api/v1/history/stats")
    assert r.status_code == 200
    data = r.json()
    assert "total_downloads" in data
    assert "downloads_by_status" in data


def test_clear_history(client):
    c, mock = client
    r = c.delete("/api/v1/history")
    assert r.status_code == 200
    data = r.json()
    assert "deleted_count" in data


def test_get_config(client):
    c, mock = client
    r = c.get("/api/v1/config")
    assert r.status_code == 200
    data = r.json()
    assert "app" in data
    assert "download" in data
    assert "video" in data


def test_update_config(client):
    c, mock = client
    r = c.put("/api/v1/config", json={"max_concurrent_downloads": 5})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True


def test_openapi_complete(client):
    """Endpoints we implement are present in OpenAPI schema."""
    c, mock = client
    r = c.get("/openapi.json")
    assert r.status_code == 200
    paths = list(r.json()["paths"].keys())

    expected = [
        "/api/v1/health",
        "/api/v1/health/ready",
        "/api/v1/health/disk",
        "/api/v1/features",
        "/api/v1/features/tiers",
        "/api/v1/features/tiers/{tier_name}/limits",
        "/api/v1/usage",
        "/api/v1/usage/reset",
        "/api/v1/history",
        "/api/v1/history/stats",
        "/api/v1/config",
    ]
    for ep in expected:
        assert ep in paths, f"Missing endpoint: {ep}"
