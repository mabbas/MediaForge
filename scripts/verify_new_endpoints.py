"""Verify new endpoints return 200 (run with API DB env set)."""
import os
os.environ["GID_API_DATABASE_URL"] = "sqlite+aiosqlite:///./data/test.db"
os.environ["GID_API_SKIP_SEED"] = "1"  # avoid seed for quick verification

# Force fresh config before app import
from api.config import get_api_settings
get_api_settings.cache_clear()

from fastapi.testclient import TestClient
from api.main import app

endpoints = [
    ("GET", "/api/v1/health/network"),
    ("GET", "/api/v1/transcripts"),
    ("GET", "/api/v1/features"),
    ("GET", "/api/v1/history"),
    ("GET", "/api/v1/usage"),
    ("GET", "/api/v1/config"),
    ("GET", "/api/v1/queue/stats"),
]

with TestClient(app) as client:
    for method, path in endpoints:
        r = client.get(path)
        status = "OK" if r.status_code == 200 else "FAIL"
        print(f"  {status} {method} {path}: {r.status_code}")

print("NEW ENDPOINTS OK")
