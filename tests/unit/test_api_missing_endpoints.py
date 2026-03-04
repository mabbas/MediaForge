"""Tests for newly added endpoints."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


class _FakeApp:
    """Minimal fake app for endpoint existence tests."""

    def __init__(self):
        self._engine = MagicMock()
        self._usage = MagicMock()
        self._usage.get_all_usage.return_value = {}
        self._usage.get_usage.return_value = 0
        self._usage.get_remaining.return_value = -1
        self.get_stats = MagicMock(return_value={
            "active": 0, "max_concurrent": 3,
            "queue": {"total": 0, "high": 0, "normal": 0, "low": 0},
            "total_jobs": 0, "is_paused": False, "jobs_by_status": {},
        })
        self.list_providers = MagicMock(return_value=[])
        self.get_all_jobs = MagicMock(return_value=[])
        self.get_job = MagicMock(return_value=None)

    def set_bandwidth_limit(self, _: int) -> None:
        pass


@pytest.fixture
def client(tmp_path):
    """Test client with SQLite and mocked app."""
    db_path = (tmp_path / "test.db").as_posix()
    os.environ["GID_API_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["GID_API_SKIP_SEED"] = "1"
    # Force fresh settings so lifespan uses this DB URL
    from api.config import get_api_settings
    get_api_settings.cache_clear()
    import api.dependencies as deps
    deps._app_instance = None
    mock_app = _FakeApp()

    from api.main import app
    from api.database.connection import get_session
    from api.dependencies import get_app

    async def _mock_session() -> AsyncGenerator[AsyncSession, None]:
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
        session.get = AsyncMock(return_value=None)
        yield session

    app.dependency_overrides[get_session] = _mock_session
    app.dependency_overrides[get_app] = lambda: mock_app
    try:
        with TestClient(app) as c:
            yield c, mock_app
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_app, None)


def test_resume_help(client):
    """Resume endpoint exists."""
    c, mock = client
    r = c.get("/openapi.json")
    paths = r.json()["paths"]
    resume_paths = [p for p in paths if "/resume" in p]
    assert len(resume_paths) > 0


def test_file_endpoint_exists(client):
    """File serve endpoint exists."""
    c, mock = client
    r = c.get("/openapi.json")
    paths = r.json()["paths"]
    file_paths = [p for p in paths if "/file" in p]
    assert len(file_paths) > 0


def test_network_status(client):
    """Network status endpoint works."""
    c, mock = client
    r = c.get("/api/v1/health/network")
    assert r.status_code == 200
    data = r.json()
    assert "online" in data


def test_transcript_list(client):
    """Transcript list endpoint works."""
    c, mock = client
    r = c.get("/api/v1/transcripts")
    assert r.status_code == 200


def test_transcript_not_found(client):
    """Transcript get returns 404."""
    c, mock = client
    r = c.get("/api/v1/transcripts/nonexistent")
    assert r.status_code == 404


def test_full_endpoint_count(client):
    """All expected endpoints present (resume, file, network, transcripts)."""
    c, mock = client
    r = c.get("/openapi.json")
    paths = r.json()["paths"]

    # Current API: 20 path entries (includes downloads, health, transcripts, etc.)
    assert len(paths) >= 20, f"Only {len(paths)} paths, expected 20+"

    total_ops = sum(
        1
        for p in paths
        for k in r.json()["paths"][p]
        if k in ("get", "post", "put", "delete", "patch")
    )
    print(f"Total paths: {len(paths)}")
    print(f"Total operations: {total_ops}")
    assert total_ops >= 22, f"Only {total_ops} operations, expected 22+"
