"""GrabItDown API Smoke Test

Run with: python scripts/api_smoke_test.py
Requires the API to be running (make api-dev).
"""

import sys

import requests

BASE = "http://localhost:8000/api/v1"


def run_tests():
    passed = 0
    failed = 0

    tests = [
        ("Health", "GET", f"{BASE}/health", None, 200),
        ("Readiness", "GET", f"{BASE}/health/ready", None, 200),
        ("Disk", "GET", f"{BASE}/health/disk", None, 200),
        ("Network", "GET", f"{BASE}/health/network", None, 200),
        ("Features", "GET", f"{BASE}/features", None, 200),
        ("Tiers", "GET", f"{BASE}/features/tiers", None, 200),
        ("Basic Limits", "GET", f"{BASE}/features/tiers/basic/limits", None, 200),
        ("Config", "GET", f"{BASE}/config", None, 200),
        ("Providers", "GET", f"{BASE}/providers", None, 200),
        ("Usage", "GET", f"{BASE}/usage", None, 200),
        ("History", "GET", f"{BASE}/history", None, 200),
        ("History Stats", "GET", f"{BASE}/history/stats", None, 200),
        ("Queue Stats", "GET", f"{BASE}/queue/stats", None, 200),
        ("Downloads List", "GET", f"{BASE}/downloads", None, 200),
        ("Transcripts List", "GET", f"{BASE}/transcripts", None, 200),
        (
            "Resolve URL",
            "POST",
            f"{BASE}/resolve",
            {"url": "https://youtube.com/watch?v=dQw4w9WgXcQ"},
            200,
        ),
        ("OpenAPI", "GET", "http://localhost:8000/openapi.json", None, 200),
    ]

    for name, method, url, body, expected in tests:
        try:
            if method == "GET":
                r = requests.get(url, timeout=10)
            elif method == "POST":
                r = requests.post(url, json=body, timeout=10)

            if r.status_code == expected:
                print(f"  ✓ {name}: {r.status_code}")
                passed += 1
            else:
                print(f"  ✗ {name}: {r.status_code} (expected {expected})")
                print(f"    {r.text[:200]}")
                failed += 1
        except requests.ConnectionError:
            print(f"  ✗ {name}: Connection refused (is API running?)")
            failed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")

    if failed == 0:
        print("API SMOKE TEST PASSED ✓")
    else:
        print("API SMOKE TEST FAILED ✗")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    print("GrabItDown API Smoke Test")
    print(f"Testing against: {BASE}")
    print("=" * 40)
    sys.exit(run_tests())
