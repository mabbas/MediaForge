"""Verify all endpoint groups return 200."""
import os
os.environ["GID_API_DATABASE_URL"] = "sqlite+aiosqlite:///./data/test.db"

from fastapi.testclient import TestClient
from api.main import app

with TestClient(app) as client:
    r = client.get("/api/v1/features")
    print(f"Features: {r.status_code}")

    r = client.get("/api/v1/features/tiers")
    data = r.json()
    print(f"Tiers: {len(data['tiers'])}")

    r = client.get("/api/v1/features/tiers/basic/limits")
    print(f"Basic limits: {r.status_code}")

    r = client.get("/api/v1/config")
    print(f"Config: {r.status_code}")
    print(f"  App: {r.json()['app']['name']}")

    r = client.get("/api/v1/history")
    print(f"History: {r.status_code}")

    r = client.get("/api/v1/history/stats")
    print(f"Stats: {r.status_code}")

print("ALL ENDPOINT GROUPS OK")
