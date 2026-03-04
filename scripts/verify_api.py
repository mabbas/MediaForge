"""Verify API OpenAPI and endpoint groups."""
import os

os.environ["GID_API_DATABASE_URL"] = "sqlite+aiosqlite:///./data/test.db"

from fastapi.testclient import TestClient
from api.main import app

with TestClient(app) as client:

    # 1. OpenAPI
    r = client.get("/openapi.json")
    schema = r.json()
    paths = sorted(schema["paths"].keys())
    print(f"Total endpoints: {len(paths)}")
    for p in paths:
        methods = [m.upper() for m in schema["paths"][p] if m in ("get", "post", "put", "delete", "patch")]
        print(f"  {' '.join(methods):20s} {p}")
    tags = [t["name"] for t in schema.get("tags", [])]
    print(f"\nTags ({len(tags)}): {tags}")
    total_ops = sum(1 for p in paths for k in schema["paths"][p] if k in ("get", "post", "put", "delete", "patch"))
    print(f"Total operations: {total_ops}")
print("\nFULL API COMPLETE")
