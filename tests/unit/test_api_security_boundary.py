"""Comprehensive security boundary tests for MediaForge API (STEP 02-A).

Verifies:
1. Deny-by-default fail-closed behavior for all protected endpoints (anonymous -> 401).
2. Allowlisted public probes (/health, /health/ready, /docs, /openapi.json, /).
3. Rejection of unverified Bearer tokens and client-forged tenant/user headers.
4. Role-based access control (RBAC) enforcement (e.g. member -> 403 on admin actions).
5. TenantContext isolation and authorization checks.
6. WebSocket authentication enforcement (anonymous/invalid -> 1008 policy violation).
"""

from __future__ import annotations

import os
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.dependencies import (
    AuthMethod,
    AuthenticatedPrincipal,
    CurrentUser,
    TenantContext,
    get_current_user,
    require_role,
    require_tier,
)


@pytest.fixture
def client(tmp_path):
    """Test client with SQLite test database."""
    db_path = (tmp_path / "sec_test.db").as_posix()
    os.environ["GID_API_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["GID_API_SKIP_SEED"] = "1"
    from api.config import get_api_settings
    get_api_settings.cache_clear()

    from api.main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Deny-by-default anonymous access tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/downloads"),
        ("POST", "/api/v1/downloads"),
        ("POST", "/api/v1/downloads/batch"),
        ("POST", "/api/v1/downloads/playlist"),
        ("POST", "/api/v1/clips/extract"),
        ("POST", "/api/v1/clips/validate"),
        ("POST", "/api/v1/clips/merge"),
        ("GET", "/api/v1/history"),
        ("GET", "/api/v1/history/stats"),
        ("DELETE", "/api/v1/history"),
        ("GET", "/api/v1/config"),
        ("PUT", "/api/v1/config"),
        ("GET", "/api/v1/features"),
        ("GET", "/api/v1/features/tiers"),
        ("GET", "/api/v1/features/tiers/basic/limits"),
        ("GET", "/api/v1/usage"),
        ("POST", "/api/v1/usage/reset"),
        ("GET", "/api/v1/transcripts"),
        ("POST", "/api/v1/transcripts/extract"),
        ("GET", "/api/v1/queue/stats"),
        ("POST", "/api/v1/queue/pause"),
        ("POST", "/api/v1/queue/resume"),
        ("POST", "/api/v1/resolve"),
        ("GET", "/api/v1/providers"),
        ("GET", "/api/v1/health/disk"),
        ("GET", "/api/v1/health/network"),
    ],
)
def test_anonymous_requests_denied(client: TestClient, method: str, path: str):
    """Protected endpoints strictly return 401 Unauthorized for anonymous callers."""
    if method == "GET":
        r = client.get(path)
    elif method == "POST":
        r = client.post(path, json={})
    elif method == "PUT":
        r = client.put(path, json={})
    elif method == "DELETE":
        r = client.delete(path)
    else:
        pytest.fail(f"Unsupported method: {method}")

    assert r.status_code == 401, f"{method} {path} returned {r.status_code}, expected 401"
    assert "WWW-Authenticate" in r.headers
    assert r.headers["WWW-Authenticate"] == "Bearer"


# ---------------------------------------------------------------------------
# 2. Allowlisted public endpoints
# ---------------------------------------------------------------------------

def test_health_public_unauthenticated(client: TestClient):
    """GET /api/v1/health is public and returns 200 without credentials."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_health_ready_public_unauthenticated(client: TestClient):
    """GET /api/v1/health/ready is public and does not require credentials."""
    r = client.get("/api/v1/health/ready")
    assert r.status_code == 200
    assert "database" in r.json()


def test_docs_and_openapi_public(client: TestClient):
    """OpenAPI and Swagger docs are accessible unauthenticated."""
    r_openapi = client.get("/openapi.json")
    assert r_openapi.status_code == 200
    r_docs = client.get("/docs")
    assert r_docs.status_code == 200


def test_root_redirect_public(client: TestClient):
    """Root URL redirects without authentication."""
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307)


# ---------------------------------------------------------------------------
# 3. Forged client header and invalid token rejection
# ---------------------------------------------------------------------------

def test_forged_headers_ignored(client: TestClient):
    """Client-provided X-User-ID or X-Tenant-ID without valid Bearer return 401."""
    r = client.get(
        "/api/v1/downloads",
        headers={
            "X-User-ID": "00000000-0000-0000-0000-000000000002",
            "X-Tenant-ID": "00000000-0000-0000-0000-000000000001",
            "X-User-Role": "owner",
        },
    )
    assert r.status_code == 401


def test_invalid_bearer_token_rejected(client: TestClient):
    """Supplying an unverified Bearer token returns 401 fail-closed."""
    r = client.get(
        "/api/v1/features",
        headers={"Authorization": "Bearer fake.jwt.token"},
    )
    assert r.status_code == 401
    assert "Invalid authentication credentials" in r.json().get("detail", "")


# ---------------------------------------------------------------------------
# 4. RBAC and Role Enforcement
# ---------------------------------------------------------------------------

def test_put_config_requires_admin_or_owner(client: TestClient):
    """PUT /api/v1/config returns 403 Forbidden for member role."""
    from api.main import app

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user-member",
        tenant_id="tenant-1",
        role="member",
        tier="platinum",
    )
    try:
        r = client.put("/api/v1/config", json={"max_concurrent_downloads": 5})
        assert r.status_code == 403
        assert r.json()["detail"] == "Admin privileges required"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_post_usage_reset_requires_admin_or_owner(client: TestClient):
    """POST /api/v1/usage/reset returns 403 Forbidden for member role."""
    from api.main import app

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user-member",
        tenant_id="tenant-1",
        role="member",
        tier="platinum",
    )
    try:
        r = client.post("/api/v1/usage/reset")
        assert r.status_code == 403
        assert r.json()["detail"] == "Admin privileges required"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_require_role_dependency():
    """require_role dependency factory checks role permissions."""
    checker = require_role(["admin", "owner"])

    user_admin = CurrentUser(role="admin")
    result = await checker(user=user_admin)
    assert result == user_admin

    user_member = CurrentUser(role="member")
    with pytest.raises(HTTPException) as exc:
        await checker(user=user_member)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_tier_dependency():
    """require_tier dependency factory enforces tier levels."""
    checker = require_tier("pro")

    user_basic = CurrentUser(tier="basic")
    with pytest.raises(HTTPException) as exc:
        await checker(user=user_basic)
    assert exc.value.status_code == 403

    user_pro = CurrentUser(tier="pro")
    result = await checker(user=user_pro)
    assert result == user_pro

    user_plat = CurrentUser(tier="platinum")
    result = await checker(user=user_plat)
    assert result == user_plat


# ---------------------------------------------------------------------------
# 5. TenantContext Isolation Checks
# ---------------------------------------------------------------------------

def test_tenant_context_isolation():
    """TenantContext verifies caller tenant against target resource tenant."""
    member_principal = AuthenticatedPrincipal(
        user_id="user-1",
        tenant_id="tenant-alpha",
        role="member",
    )
    ctx = TenantContext(tenant_id="tenant-alpha", principal=member_principal)

    # Allowed: target matches principal tenant
    assert ctx.check_tenant_access("tenant-alpha") is True

    # Forbidden: target is another tenant
    with pytest.raises(HTTPException) as exc:
        ctx.check_tenant_access("tenant-beta")
    assert exc.value.status_code == 403

    # Allowed for system owner
    owner_principal = AuthenticatedPrincipal(
        user_id="user-owner",
        tenant_id="00000000-0000-0000-0000-000000000001",
        role="owner",
        is_system_owner=True,
    )
    owner_ctx = TenantContext(
        tenant_id="00000000-0000-0000-0000-000000000001", principal=owner_principal
    )
    assert owner_ctx.check_tenant_access("tenant-beta") is True


# ---------------------------------------------------------------------------
# 6. WebSocket Authentication
# ---------------------------------------------------------------------------

def test_websocket_anonymous_closed(client: TestClient):
    """Anonymous WebSocket connection without auth is closed with code 1008."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/v1/ws/progress") as ws:
            ws.send_text('{"type": "subscribe_all"}')
            ws.receive_text()
    assert exc.value.code == 1008


def test_websocket_invalid_token_closed(client: TestClient):
    """WebSocket connection with invalid query token is closed with code 1008."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/v1/ws/progress?token=bad-token") as ws:
            ws.receive_text()
    assert exc.value.code == 1008


def test_websocket_authenticated_accepted(client: TestClient):
    """WebSocket connection with valid authenticated principal stays connected."""
    from api.main import app

    app.dependency_overrides[get_current_user] = lambda: CurrentUser()
    try:
        with client.websocket_connect("/api/v1/ws/progress") as ws:
            ws.send_text('{"type": "subscribe_all"}')
    finally:
        app.dependency_overrides.pop(get_current_user, None)
