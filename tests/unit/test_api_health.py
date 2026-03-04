"""Tests for health endpoints."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """Test client with SQLite database (tmp_path so directory exists)."""
    db_path = (tmp_path / "test.db").as_posix()
    os.environ["GID_API_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["GID_API_SKIP_SEED"] = "1"
    from api.config import get_api_settings
    get_api_settings.cache_clear()
    from api.main import app

    with TestClient(app) as c:
        yield c


def test_health(client: TestClient):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_health_disk(client: TestClient):
    r = client.get("/api/v1/health/disk")
    assert r.status_code == 200
    assert "free_gb" in r.json()


def test_root_redirect(client: TestClient):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307)


def test_openapi(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "GrabItDown API"


def test_swagger(client: TestClient):
    r = client.get("/docs")
    assert r.status_code == 200


def test_process_time_header(client: TestClient):
    r = client.get("/api/v1/health")
    assert "x-process-time" in r.headers
