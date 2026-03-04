"""Run API audit verification."""
import os
os.environ["GID_API_DATABASE_URL"] = "sqlite+aiosqlite:///./data/test.db"

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

r = client.get("/openapi.json")
schema = r.json()
paths = sorted(schema["paths"].keys())
print(f"Total endpoint paths: {len(paths)}")
for p in paths:
    methods = list(schema["paths"][p].keys())
    meth_str = " ".join(m.upper() for m in methods)
    print(f"  {meth_str:10s} {p}")

total_ops = sum(len(schema["paths"][p]) for p in paths)
print(f"\nTotal operations: {total_ops}")
assert len(paths) >= 20, f"Expected 20+ paths, got {len(paths)}"
assert total_ops >= 22, f"Expected 22+ operations, got {total_ops}"

tags = [t["name"] for t in schema.get("tags", [])]
print(f"Tags ({len(tags)}): {tags}")

print("\nFINAL API AUDIT OK")
