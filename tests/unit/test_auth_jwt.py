"""Unit tests for Argon2id password handling and verified JWT authentication (STEP 02-B1)."""

from __future__ import annotations

import asyncio
import os
import time
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.database.connection import Base, get_session
from api.database.models import TenantDB, UserDB
from api.dependencies import CurrentUser, get_current_user
from api.main import app
from api.security.jwt import (
    TokenAlgorithmError,
    TokenExpiredError,
    TokenInvalidError,
    create_access_token,
    decode_and_verify_access_token,
)
from api.security.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    check_needs_rehash,
    hash_password,
    verify_password,
)


# ---------------------------------------------------------------------------
# 1. Password Hashing (Argon2id) Tests
# ---------------------------------------------------------------------------

def test_password_hashing_argon2id():
    """Verify password hashing produces valid Argon2id hashes."""
    pwd = "CorrectHorseBatteryStaple123!"
    h1 = hash_password(pwd)
    h2 = hash_password(pwd)

    assert h1.startswith("$argon2id$")
    assert h2.startswith("$argon2id$")
    # Unique salts per hash
    assert h1 != h2

    # Verification
    assert verify_password(pwd, h1) is True
    assert verify_password("WrongPassword!", h1) is False
    assert verify_password("", h1) is False
    assert verify_password(pwd, None) is False
    assert verify_password(pwd, "invalid$format$hash") is False


def test_password_length_limits():
    """Enforce password length boundaries to prevent DoS via huge inputs."""
    with pytest.raises(ValueError, match="at least"):
        hash_password("short")

    with pytest.raises(ValueError, match="exceeds maximum"):
        hash_password("A" * 129)

    # Valid boundaries
    min_pwd = "A" * MIN_PASSWORD_LENGTH
    max_pwd = "A" * MAX_PASSWORD_LENGTH
    assert verify_password(min_pwd, hash_password(min_pwd)) is True
    assert verify_password(max_pwd, hash_password(max_pwd)) is True

    # Verification fails safely on oversized plaintext input
    h = hash_password("ValidPassword123")
    assert verify_password("A" * 200, h) is False


def test_check_needs_rehash():
    """Verify check_needs_rehash functionality."""
    pwd = "ValidPassword123"
    h = hash_password(pwd)
    assert check_needs_rehash(h) is False
    assert check_needs_rehash(None) is True
    assert check_needs_rehash("corrupted") is True


# ---------------------------------------------------------------------------
# 2. JWT Issuance and Verification Tests
# ---------------------------------------------------------------------------

def test_jwt_issuance_and_decoding():
    """Verify access token generation with authoritative claims and expiration."""
    user_id = "test-user-123"
    tenant_id = "test-tenant-456"

    token, expires_in = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        expires_in_seconds=900,
    )
    assert isinstance(token, str)
    assert expires_in == 900

    payload = decode_and_verify_access_token(token)
    assert payload["sub"] == user_id
    assert payload["tid"] == tenant_id
    assert payload["iss"] == "mediaforge"
    assert payload["aud"] == "mediaforge-api"
    assert payload["typ"] == "access"
    assert payload["exp"] - payload["iat"] == 900


def test_jwt_reject_expired():
    """Verify expired token is rejected with TokenExpiredError."""
    user_id = "test-user"
    tenant_id = "test-tenant"
    # Negative lifetime -> immediately expired
    token, _ = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        expires_in_seconds=-10,
    )
    with pytest.raises(TokenExpiredError):
        decode_and_verify_access_token(token)


def test_jwt_reject_tampered_signature():
    """Verify modified payload or signature raises TokenInvalidError."""
    token, _ = create_access_token("u1", "t1")
    tampered = token[:-4] + "abcd"
    with pytest.raises(TokenInvalidError):
        decode_and_verify_access_token(tampered)


def test_jwt_reject_invalid_algorithm():
    """Verify rejection of tokens using 'none' or unpermitted algorithms."""
    import jwt as pyjwt
    # Construct an unsigned token with alg=none
    payload = {
        "sub": "u1",
        "tid": "t1",
        "iss": "mediaforge",
        "aud": "mediaforge-api",
        "iat": int(time.time()),
        "exp": int(time.time()) + 900,
        "typ": "access",
    }
    none_token = pyjwt.encode(payload, key="", algorithm="none")
    with pytest.raises(TokenAlgorithmError):
        decode_and_verify_access_token(none_token)


def test_jwt_reject_wrong_issuer_and_audience():
    """Verify token with wrong audience or issuer is rejected."""
    token, _ = create_access_token(
        "u1",
        "t1",
        extra_claims={"aud": "wrong-audience"},
    )
    with pytest.raises(TokenInvalidError):
        decode_and_verify_access_token(token)

    token_iss, _ = create_access_token(
        "u1",
        "t1",
        extra_claims={"iss": "wrong-issuer"},
    )
    with pytest.raises(TokenInvalidError):
        decode_and_verify_access_token(token_iss)


# ---------------------------------------------------------------------------
# 3. Database Fixture & Endpoint Integration Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_env(tmp_path):
    """Set up isolated SQLite DB with pre-seeded tenant and user records."""
    db_path = (tmp_path / "auth_test.db").as_posix()
    db_url = f"sqlite+aiosqlite:///{db_path}"
    old_db_url = os.environ.get("GID_API_DATABASE_URL")
    old_skip_seed = os.environ.get("GID_API_SKIP_SEED")
    old_jwt_key = os.environ.get("GID_JWT_SECRET_KEY")
    os.environ["GID_API_DATABASE_URL"] = db_url
    os.environ["GID_API_SKIP_SEED"] = "1"
    os.environ["GID_JWT_SECRET_KEY"] = "super-secret-test-jwt-signing-key-minimum-32-chars!"

    from api.config import get_api_settings
    get_api_settings.cache_clear()

    engine = create_async_engine(
        db_url,
        execution_options={"schema_translate_map": {"gid": None}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    user_pwd = "SecureTestPassword123!"
    user_hash = hash_password(user_pwd)

    async def _setup_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            # 1. Default tenant
            tenant = TenantDB(
                id="tenant-default-001",
                name="Default Tenant",
                slug="default",
                tier="platinum",
                is_active=True,
            )
            session.add(tenant)

            # 2. Inactive tenant
            tenant_inactive = TenantDB(
                id="tenant-inactive-002",
                name="Inactive Tenant",
                slug="inactive-tenant",
                tier="basic",
                is_active=False,
            )
            session.add(tenant_inactive)

            # 3. Active user with Argon2id password
            user = UserDB(
                id="user-active-001",
                tenant_id=tenant.id,
                email="alice@mediaforge.local",
                display_name="Alice User",
                password_hash=user_hash,
                role="member",
                is_active=True,
            )
            session.add(user)

            # 4. Admin user
            admin = UserDB(
                id="user-admin-002",
                tenant_id=tenant.id,
                email="admin@mediaforge.local",
                display_name="Admin User",
                password_hash=user_hash,
                role="admin",
                is_active=True,
            )
            session.add(admin)

            # 5. Inactive/disabled user
            disabled_user = UserDB(
                id="user-disabled-003",
                tenant_id=tenant.id,
                email="disabled@mediaforge.local",
                display_name="Disabled User",
                password_hash=user_hash,
                role="member",
                is_active=False,
            )
            session.add(disabled_user)

            # 6. User in inactive tenant
            orphan_user = UserDB(
                id="user-orphan-004",
                tenant_id=tenant_inactive.id,
                email="orphan@mediaforge.local",
                display_name="Orphan User",
                password_hash=user_hash,
                role="member",
                is_active=True,
            )
            session.add(orphan_user)

            await session.commit()

    asyncio.run(_setup_db())

    async def _mock_get_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _mock_get_session

    from api.services.rate_limiter import RateLimiter

    with patch.object(RateLimiter, "check_login_ip", new_callable=AsyncMock, return_value=(True, 0)), \
         patch.object(RateLimiter, "check_login_email", new_callable=AsyncMock, return_value=(True, 0)):
        with TestClient(app) as client:
            yield client, user_pwd

    app.dependency_overrides.pop(get_session, None)
    asyncio.run(engine.dispose())

    from dotenv import dotenv_values
    from src.env_loader import _project_root
    root = _project_root()
    deploy_env = root / "deploy" / ".env"
    restored_db = False
    if deploy_env.is_file():
        vals = dotenv_values(str(deploy_env))
        if "GID_API_DATABASE_URL" in vals and vals["GID_API_DATABASE_URL"]:
            os.environ["GID_API_DATABASE_URL"] = vals["GID_API_DATABASE_URL"]
            restored_db = True
    if not restored_db:
        if old_db_url is not None:
            os.environ["GID_API_DATABASE_URL"] = old_db_url
        else:
            os.environ.pop("GID_API_DATABASE_URL", None)

    if old_skip_seed is not None:
        os.environ["GID_API_SKIP_SEED"] = old_skip_seed
    else:
        os.environ.pop("GID_API_SKIP_SEED", None)
    if old_jwt_key is not None:
        os.environ["GID_JWT_SECRET_KEY"] = old_jwt_key
    else:
        os.environ.pop("GID_JWT_SECRET_KEY", None)
    get_api_settings.cache_clear()


def test_login_success(auth_env):
    """POST /api/v1/auth/login succeeds with valid credentials and issues JWT."""
    client, password = auth_env

    r = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@mediaforge.local", "password": password},
    )
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900

    # Token is valid and verifiable
    payload = decode_and_verify_access_token(data["access_token"])
    assert payload["sub"] == "user-active-001"
    assert payload["tid"] == "tenant-default-001"


def test_login_wrong_password_generic_error(auth_env):
    """Wrong password returns generic 401 Unauthorized."""
    client, _ = auth_env
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@mediaforge.local", "password": "WrongPassword123!"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"
    assert "WWW-Authenticate" in r.headers


def test_login_unknown_user_generic_error(auth_env):
    """Non-existent user returns generic 401 without revealing account absence."""
    client, password = auth_env
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "unknown@mediaforge.local", "password": password},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"


def test_login_disabled_user_rejected(auth_env):
    """Disabled user cannot log in and receives generic 401."""
    client, password = auth_env
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "disabled@mediaforge.local", "password": password},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"


def test_auth_me_profile_safe(auth_env):
    """GET /api/v1/auth/me returns safe profile information without secrets."""
    client, password = auth_env

    # 1. Login
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@mediaforge.local", "password": password},
    )
    token = login_resp.json()["access_token"]

    # 2. Call /auth/me
    r = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == "user-active-001"
    assert data["tenant_id"] == "tenant-default-001"
    assert data["email"] == "alice@mediaforge.local"
    assert data["display_name"] == "Alice User"
    assert data["role"] == "member"
    assert data["tier"] == "platinum"
    assert data["auth_method"] == "jwt"

    # Crucial security assertion: secrets are never leaked
    assert "password" not in data
    assert "password_hash" not in data
    assert "secret" not in data


def test_protected_route_accepts_verified_token(auth_env):
    """Protected API routes accept genuine access tokens."""
    client, password = auth_env

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@mediaforge.local", "password": password},
    )
    token = login_resp.json()["access_token"]

    r = client.get(
        "/api/v1/features",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert "features" in r.json()


def test_authoritative_database_role_enforcement(auth_env):
    """Token with forged role claim cannot elevate privileges; DB record rules."""
    client, _ = auth_env

    # Forge a token claiming role="owner" for user-active-001 (who is only "member" in DB)
    forged_token, _ = create_access_token(
        user_id="user-active-001",
        tenant_id="tenant-default-001",
        extra_claims={"role": "owner"},
    )

    # PUT /api/v1/config requires admin or owner role
    r = client.put(
        "/api/v1/config",
        json={"max_concurrent_downloads": 8},
        headers={"Authorization": f"Bearer {forged_token}"},
    )
    # Database authoritative lookup finds user is 'member', so 403 Forbidden is returned!
    assert r.status_code == 403
    assert r.json()["detail"] == "Admin privileges required"


def test_forged_tenant_claim_in_token_rejected(auth_env):
    """Token with mismatched tenant claim is rejected by authoritative DB check."""
    client, _ = auth_env

    # Token claims tenant is 'other-tenant', but DB says 'tenant-default-001'
    mismatched_token, _ = create_access_token(
        user_id="user-active-001",
        tenant_id="forged-foreign-tenant-id",
    )

    r = client.get(
        "/api/v1/features",
        headers={"Authorization": f"Bearer {mismatched_token}"},
    )
    assert r.status_code == 401
    assert "Token tenant claims mismatch" in r.json()["detail"]


def test_disabled_user_token_rejected_at_runtime(auth_env):
    """Active token is immediately rejected if user is deactivated in database."""
    client, _ = auth_env

    # Token issued for disabled user
    token, _ = create_access_token(
        user_id="user-disabled-003",
        tenant_id="tenant-default-001",
    )

    r = client.get(
        "/api/v1/features",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401
    assert "inactive" in r.json()["detail"]


def test_websocket_inband_jwt_authentication(auth_env):
    """WebSocket accepts valid JWT via in-band auth message; rejects invalid."""
    client, password = auth_env
    from starlette.websockets import WebSocketDisconnect

    # 1. Login to get real token
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@mediaforge.local", "password": password},
    )
    valid_token = login_resp.json()["access_token"]

    # 2. In-band authentication succeeds
    with client.websocket_connect("/api/v1/ws/progress") as ws:
        ws.send_text(f'{{"type": "auth", "token": "{valid_token}"}}')
        ws.send_text('{"type": "subscribe_all"}')

    # 3. In-band authentication with invalid token gets closed with 1008
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/v1/ws/progress") as ws:
            ws.send_text('{"type": "auth", "token": "invalid.jwt.token"}')
            ws.receive_text()
    assert exc.value.code == 1008
