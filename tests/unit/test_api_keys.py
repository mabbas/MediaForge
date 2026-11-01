"""Unit and security test suite for MediaForge Commercial API Keys, Scoped Authorization, and Lifecycle.

Milestone: STEP 02-C2
Tests:
 1. Key creation by tenant owner/admin succeeds, returns raw secret.
 2. Secret displayed only once upon creation; never retrievable afterwards.
 3. Valid key authorizes allowed route.
 4. Missing or invalid keys return HTTP 401 Unauthorized; ambiguous credentials return HTTP 400.
 5. Insufficient scope returns HTTP 403 Forbidden.
 6. Revoked and expired keys return HTTP 401 Unauthorized.
 7. Tenant A key cannot access Tenant B data (anti-enumeration returns HTTP 404).
 8. Tenant members (regular users) cannot manage API keys (HTTP 403).
 9. API keys cannot manage API keys (HTTP 403).
10. API keys cannot perform system-level operations (HTTP 403).
11. Read-only keys cannot create download jobs (HTTP 403).
12. Dual rate limits (per-key and tenant aggregate) enforced with HTTP 429.
13. Redis outage fails closed (HTTP 503 Service Unavailable).
14. Concurrent/immediate revocation takes effect instantly (HTTP 401).
15. Existing JWT authentication and session lifecycle unaffected.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base, get_session
from api.database.models import APIKeyDB, DownloadJobDB, TenantDB, TranscriptDB, UserDB
from api.dependencies import AuthMethod, CurrentUser, get_current_user
from api.security.jwt import create_access_token
from api.security.passwords import hash_password
from api.services.rate_limiter import RateLimiter, RateLimiterUnavailableError


@pytest.fixture
def api_keys_env(tmp_path):
    """Setup isolated multi-tenant database environment for API key lifecycle testing."""
    db_file = tmp_path / "api_keys_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    old_db = os.environ.get("GID_API_DATABASE_URL")
    old_jwt = os.environ.get("GID_JWT_SECRET_KEY")
    os.environ["GID_API_DATABASE_URL"] = db_url
    os.environ["GID_JWT_SECRET_KEY"] = "api-keys-test-secret-key-at-least-32-bytes!"
    os.environ["GID_API_SKIP_SEED"] = "1"

    from api.config import get_api_settings
    get_api_settings.cache_clear()

    engine = create_async_engine(
        db_url,
        echo=False,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    tenant_a_id = "tenant-alpha-001"
    tenant_b_id = "tenant-beta-002"

    async def _setup_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            # Tenants
            ta = TenantDB(id=tenant_a_id, name="Tenant Alpha", slug="alpha", tier="platinum", is_active=True)
            tb = TenantDB(id=tenant_b_id, name="Tenant Beta", slug="beta", tier="pro", is_active=True)
            session.add_all([ta, tb])

            # Password hash for logins
            pwd_hash = hash_password("ValidPass123!")

            # Tenant A Users
            ua_owner = UserDB(
                id="user-a-owner",
                tenant_id=tenant_a_id,
                email="owner@alpha.local",
                password_hash=pwd_hash,
                role="owner",
                is_active=True,
            )
            ua_admin = UserDB(
                id="user-a-admin",
                tenant_id=tenant_a_id,
                email="admin@alpha.local",
                password_hash=pwd_hash,
                role="admin",
                is_active=True,
            )
            ua_member = UserDB(
                id="user-a-member",
                tenant_id=tenant_a_id,
                email="member@alpha.local",
                password_hash=pwd_hash,
                role="member",
                is_active=True,
            )

            # Tenant B Users
            ub_owner = UserDB(
                id="user-b-owner",
                tenant_id=tenant_b_id,
                email="owner@beta.local",
                password_hash=pwd_hash,
                role="owner",
                is_active=True,
            )
            ub_member = UserDB(
                id="user-b-member",
                tenant_id=tenant_b_id,
                email="member@beta.local",
                password_hash=pwd_hash,
                role="member",
                is_active=True,
            )

            session.add_all([ua_owner, ua_admin, ua_member, ub_owner, ub_member])
            await session.commit()

    asyncio.run(_setup_db())

    from api.main import app

    async def _mock_get_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _mock_get_session

    users = {
        "a_owner": CurrentUser(user_id="user-a-owner", tenant_id=tenant_a_id, email="owner@alpha.local", role="owner", tier="platinum"),
        "a_admin": CurrentUser(user_id="user-a-admin", tenant_id=tenant_a_id, email="admin@alpha.local", role="admin", tier="platinum"),
        "a_member": CurrentUser(user_id="user-a-member", tenant_id=tenant_a_id, email="member@alpha.local", role="member", tier="platinum"),
        "b_owner": CurrentUser(user_id="user-b-owner", tenant_id=tenant_b_id, email="owner@beta.local", role="owner", tier="pro"),
        "b_member": CurrentUser(user_id="user-b-member", tenant_id=tenant_b_id, email="member@beta.local", role="member", tier="pro"),
    }

    tokens = {
        name: create_access_token(user_id=u.user_id, tenant_id=u.tenant_id)[0]
        for name, u in users.items()
    }

    with patch.object(RateLimiter, "check_api_key_limits", return_value=(True, "", 0)), \
         patch.object(RateLimiter, "record_api_key_usage", return_value=None), \
         patch.object(RateLimiter, "check_login_ip", return_value=(True, 0)), \
         patch.object(RateLimiter, "check_login_email", return_value=(True, 0)):
        with TestClient(app) as client:
            yield {
                "client": client,
                "session_factory": session_factory,
                "users": users,
                "tokens": tokens,
                "tenant_a_id": tenant_a_id,
                "tenant_b_id": tenant_b_id,
                "tmp_path": tmp_path,
            }

    app.dependency_overrides.pop(get_session, None)
    asyncio.run(engine.dispose())
    if old_db:
        os.environ["GID_API_DATABASE_URL"] = old_db
    else:
        os.environ.pop("GID_API_DATABASE_URL", None)
    if old_jwt:
        os.environ["GID_JWT_SECRET_KEY"] = old_jwt
    else:
        os.environ.pop("GID_JWT_SECRET_KEY", None)
    get_api_settings.cache_clear()


# ===========================================================================
# 1. Key Creation by Owner/Admin
# ===========================================================================

def test_key_creation_by_tenant_owner_and_admin(api_keys_env):
    """Scenario 1: Tenant owner and admin can create API keys and obtain raw secret."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    payload = {
        "name": "Production Key",
        "scopes": ["downloads:read", "downloads:write", "transcripts:read"],
        "rate_limit": 120,
    }

    # Owner creates key
    resp_owner = client.post(
        "/api/v1/api-keys",
        json=payload,
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert resp_owner.status_code == 201
    data_owner = resp_owner.json()
    assert data_owner["name"] == "Production Key"
    assert data_owner["key"].startswith("mf_live_")
    assert data_owner["prefix"].startswith("mf_live_")
    assert data_owner["key"].startswith(data_owner["prefix"])
    assert set(data_owner["scopes"]) == {"downloads:read", "downloads:write", "transcripts:read"}
    assert data_owner["rate_limit"] == 120
    assert "key" in data_owner

    # Admin creates key
    resp_admin = client.post(
        "/api/v1/api-keys",
        json={"name": "Admin Key", "scopes": ["usage:read"]},
        headers={"Authorization": f"Bearer {tokens['a_admin']}"},
    )
    assert resp_admin.status_code == 201
    data_admin = resp_admin.json()
    assert data_admin["key"].startswith("mf_live_")


# ===========================================================================
# 2. Secret Displayed Only Once
# ===========================================================================

def test_secret_displayed_only_once_upon_creation(api_keys_env):
    """Scenario 2: Secret is returned only once; subsequent list and get show only masked key."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Secret Test Key", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert create_resp.status_code == 201
    created_data = create_resp.json()
    key_id = created_data["id"]
    raw_key = created_data["key"]

    # 1. List keys
    list_resp = client.get(
        "/api/v1/api-keys",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert list_resp.status_code == 200
    keys = list_resp.json()["keys"]
    target_key = next((k for k in keys if k["id"] == key_id), None)
    assert target_key is not None
    assert "raw_key" not in target_key
    assert "masked_key" in target_key
    assert target_key["masked_key"] != raw_key
    assert target_key["masked_key"].endswith("****")

    # 2. Get specific key
    get_resp = client.get(
        f"/api/v1/api-keys/{key_id}",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert get_resp.status_code == 200
    single_key = get_resp.json()
    assert "raw_key" not in single_key
    assert single_key["masked_key"].endswith("****")


# ===========================================================================
# 3. Valid Key Authorizes Allowed Route
# ===========================================================================

def test_valid_key_authorizes_allowed_route(api_keys_env):
    """Scenario 3: Valid key authorizes routes matching granted scopes."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    # Create key with providers:read scope
    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Providers Key", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    raw_key = create_resp.json()["key"]

    # Call providers endpoint using X-API-Key
    resp = client.get(
        "/api/v1/providers",
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), (list, dict))


# ===========================================================================
# 4. Missing or Invalid Keys Return HTTP 401; Ambiguous Returns HTTP 400
# ===========================================================================

def test_missing_or_invalid_keys_and_ambiguous_credentials(api_keys_env):
    """Scenario 4: Missing/invalid keys return 401; both Bearer and X-API-Key return 400."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    # 1. Missing credentials
    resp_missing = client.get("/api/v1/providers")
    assert resp_missing.status_code == 401

    # 2. Empty API key
    resp_empty = client.get("/api/v1/providers", headers={"X-API-Key": "   "})
    assert resp_empty.status_code == 401

    # 3. Malformed/nonexistent API key
    resp_fake = client.get("/api/v1/providers", headers={"X-API-Key": "mf_live_invalidkey1234567890"})
    assert resp_fake.status_code == 401

    # 4. Ambiguous credentials: both Bearer and X-API-Key present
    resp_ambiguous = client.get(
        "/api/v1/providers",
        headers={
            "Authorization": f"Bearer {tokens['a_owner']}",
            "X-API-Key": "mf_live_somekey",
        },
    )
    assert resp_ambiguous.status_code == 400
    assert "Ambiguous" in resp_ambiguous.json()["detail"]


# ===========================================================================
# 5. Insufficient Scope Returns HTTP 403
# ===========================================================================

def test_insufficient_scope_returns_403(api_keys_env):
    """Scenario 5: Requesting an action without the required scope returns HTTP 403."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    # Create key with only transcripts:read
    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Transcripts Only", "scopes": ["transcripts:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    raw_key = create_resp.json()["key"]

    # Attempt to submit a download (requires downloads:write)
    resp = client.post(
        "/api/v1/downloads",
        json={"url": "https://example.com/test.mp4", "start": False},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 403
    assert "Insufficient API key permissions" in resp.json()["detail"]


# ===========================================================================
# 6. Revoked and Expired Keys Return HTTP 401
# ===========================================================================

def test_revoked_and_expired_keys_return_401(api_keys_env):
    """Scenario 6: Revoked or expired keys return HTTP 401 Unauthorized."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]
    session_factory = api_keys_env["session_factory"]

    # 1. Test Revoked Key
    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "To Be Revoked", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    key_id = create_resp.json()["id"]
    raw_key = create_resp.json()["key"]

    # Revoke key
    del_resp = client.delete(
        f"/api/v1/api-keys/{key_id}",
        params={"reason": "Compromised in CI log"},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert del_resp.status_code == 200

    # Attempt to authenticate with revoked key
    resp_revoked = client.get("/api/v1/providers", headers={"X-API-Key": raw_key})
    assert resp_revoked.status_code == 401
    assert "revoked" in resp_revoked.json()["detail"].lower()

    # 2. Test Expired Key
    create_exp_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Expiring Key", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    exp_key_id = create_exp_resp.json()["id"]
    exp_raw_key = create_exp_resp.json()["key"]

    # Manually expire in DB
    async def _expire_key():
        async with session_factory() as s:
            k = await s.get(APIKeyDB, exp_key_id)
            k.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
            await s.commit()

    asyncio.run(_expire_key())

    resp_expired = client.get("/api/v1/providers", headers={"X-API-Key": exp_raw_key})
    assert resp_expired.status_code == 401
    assert "expired" in resp_expired.json()["detail"].lower()


# ===========================================================================
# 7. Cross-Tenant Anti-Enumeration (HTTP 404)
# ===========================================================================

def test_cross_tenant_anti_enumeration(api_keys_env):
    """Scenario 7: Tenant A key cannot access or see Tenant B keys or data (returns 404)."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    # Tenant B creates an API key
    resp_b = client.post(
        "/api/v1/api-keys",
        json={"name": "Tenant B Key", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['b_owner']}"},
    )
    key_b_id = resp_b.json()["id"]

    # Tenant A user attempts to get Tenant B's key by ID -> 404
    get_cross = client.get(
        f"/api/v1/api-keys/{key_b_id}",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert get_cross.status_code == 404

    # Tenant A user attempts to delete Tenant B's key -> 404
    del_cross = client.delete(
        f"/api/v1/api-keys/{key_b_id}",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert del_cross.status_code == 404

    # Tenant A user attempts to rotate Tenant B's key -> 404
    rot_cross = client.post(
        f"/api/v1/api-keys/{key_b_id}/rotate",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert rot_cross.status_code == 404


# ===========================================================================
# 8. Tenant Members Cannot Manage API Keys
# ===========================================================================

def test_tenant_members_cannot_manage_api_keys(api_keys_env):
    """Scenario 8: Regular tenant members cannot create, list, or delete API keys."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    # Member attempts to create key
    resp_create = client.post(
        "/api/v1/api-keys",
        json={"name": "Hacked Key", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['a_member']}"},
    )
    assert resp_create.status_code == 403

    # Member attempts to list keys
    resp_list = client.get(
        "/api/v1/api-keys",
        headers={"Authorization": f"Bearer {tokens['a_member']}"},
    )
    assert resp_list.status_code == 403


# ===========================================================================
# 9. API Keys Cannot Manage API Keys
# ===========================================================================

def test_api_keys_cannot_manage_api_keys(api_keys_env):
    """Scenario 9: API keys cannot access API key management endpoints (HTTP 403)."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    # Create an API key with wildcard or all scopes
    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Service Account", "scopes": ["providers:read", "downloads:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    raw_key = create_resp.json()["key"]
    key_id = create_resp.json()["id"]

    # Attempt to list keys using API key
    resp_list = client.get(
        "/api/v1/api-keys",
        headers={"X-API-Key": raw_key},
    )
    assert resp_list.status_code == 403
    assert "API keys" in resp_list.json()["detail"]

    # Attempt to create another key using API key
    resp_create = client.post(
        "/api/v1/api-keys",
        json={"name": "Nested Key", "scopes": ["providers:read"]},
        headers={"X-API-Key": raw_key},
    )
    assert resp_create.status_code == 403

    # Attempt to rotate key using API key
    resp_rotate = client.post(
        f"/api/v1/api-keys/{key_id}/rotate",
        headers={"X-API-Key": raw_key},
    )
    assert resp_rotate.status_code == 403


# ===========================================================================
# 10. API Keys Cannot Perform System Operations
# ===========================================================================

def test_api_keys_cannot_perform_system_operations(api_keys_env):
    """Scenario 10: API keys cannot access system config or queue administrative actions."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "System Operation Attempter", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    raw_key = create_resp.json()["key"]

    # 1. GET /api/v1/config
    resp_config = client.get(
        "/api/v1/config",
        headers={"X-API-Key": raw_key},
    )
    assert resp_config.status_code == 403
    assert "system-level operations" in resp_config.json()["detail"]

    # 2. POST /api/v1/queue/pause
    resp_pause = client.post(
        "/api/v1/queue/pause",
        headers={"X-API-Key": raw_key},
    )
    assert resp_pause.status_code == 403


# ===========================================================================
# 11. Read-Only Keys Cannot Create Download Jobs
# ===========================================================================

def test_read_only_key_cannot_create_downloads(api_keys_env):
    """Scenario 11: A key with only downloads:read cannot submit downloads."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Read Only", "scopes": ["downloads:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    raw_key = create_resp.json()["key"]

    resp = client.post(
        "/api/v1/downloads",
        json={"url": "https://example.com/test.mp4", "start": False},
        headers={"X-API-Key": raw_key},
    )
    assert resp.status_code == 403
    assert "downloads:write" in resp.json()["detail"]


# ===========================================================================
# 12. Dual Rate Limits Enforced (HTTP 429)
# ===========================================================================

def test_dual_rate_limits_enforced(api_keys_env):
    """Scenario 12: Individual key limit and tenant aggregate limit return HTTP 429."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Throttled Key", "scopes": ["providers:read"], "rate_limit": 30},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    raw_key = create_resp.json()["key"]

    # Test individual key rate limit hit
    with patch.object(
        RateLimiter,
        "check_api_key_limits",
        return_value=(False, "API key rate limit of 30 requests/min exceeded", 45),
    ):
        resp_key_limit = client.get(
            "/api/v1/providers",
            headers={"X-API-Key": raw_key},
        )
        assert resp_key_limit.status_code == 429
        assert resp_key_limit.headers.get("retry-after") == "45"
        assert "exceeded" in resp_key_limit.json()["detail"]

    # Test tenant aggregate rate limit hit
    with patch.object(
        RateLimiter,
        "check_api_key_limits",
        return_value=(False, "Tenant aggregate rate limit of 300 requests/min exceeded", 50),
    ):
        resp_tenant_limit = client.get(
            "/api/v1/providers",
            headers={"X-API-Key": raw_key},
        )
        assert resp_tenant_limit.status_code == 429
        assert resp_tenant_limit.headers.get("retry-after") == "50"
        assert "Tenant aggregate rate limit" in resp_tenant_limit.json()["detail"]


# ===========================================================================
# 13. Redis Outage Fails Closed (HTTP 503)
# ===========================================================================

def test_redis_outage_fails_closed(api_keys_env):
    """Scenario 13: Redis unavailability causes API key authentication to fail closed with HTTP 503."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Outage Key", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    raw_key = create_resp.json()["key"]

    with patch.object(
        RateLimiter,
        "check_api_key_limits",
        side_effect=RateLimiterUnavailableError("Redis connection refused"),
    ):
        resp = client.get(
            "/api/v1/providers",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 503
        assert "rate limiting service unavailable" in resp.json()["detail"].lower()


# ===========================================================================
# 14. Concurrent/Immediate Revocation Takes Effect Instantly
# ===========================================================================

def test_immediate_revocation_takes_effect_instantly(api_keys_env):
    """Scenario 14: Once revoked, any immediate follow-up request with that key fails with HTTP 401."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    create_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "Instant Revoke", "scopes": ["providers:read"]},
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    key_id = create_resp.json()["id"]
    raw_key = create_resp.json()["key"]

    # Verify key works
    resp_before = client.get("/api/v1/providers", headers={"X-API-Key": raw_key})
    assert resp_before.status_code == 200

    # Revoke key
    resp_revoke = client.delete(
        f"/api/v1/api-keys/{key_id}",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert resp_revoke.status_code == 200

    # Immediate request with key must fail 401
    resp_after = client.get("/api/v1/providers", headers={"X-API-Key": raw_key})
    assert resp_after.status_code == 401
    assert "revoked" in resp_after.json()["detail"].lower()


# ===========================================================================
# 15. Existing JWT Authentication Unaffected
# ===========================================================================

def test_existing_jwt_auth_unaffected(api_keys_env):
    """Scenario 15: Existing JWT authentication, login, and user sessions continue functioning."""
    client = api_keys_env["client"]
    tokens = api_keys_env["tokens"]

    # 1. Login with password returns JWT token pair
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@alpha.local", "password": "ValidPass123!", "tenant_slug": "alpha"},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert "access_token" in token_data
    assert "refresh_token" in token_data

    # 2. Access protected endpoint with Bearer token
    resp_protected = client.get(
        "/api/v1/providers",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    assert resp_protected.status_code == 200

    # 3. User session can access /api/v1/usage
    resp_usage = client.get(
        "/api/v1/usage",
        headers={"Authorization": f"Bearer {tokens['a_owner']}"},
    )
    assert resp_usage.status_code == 200
    assert resp_usage.json()["tenant_id"] == "tenant-alpha-001"
