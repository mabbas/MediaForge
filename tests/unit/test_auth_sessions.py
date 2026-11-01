"""Unit and security tests for authentication sessions, rotating refresh tokens, replay detection, and logout (STEP 02-B2B)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api.config import get_api_settings
from api.database.connection import get_session
from api.database.models import (
    AuthSessionDB,
    ConsumedRefreshTokenDB,
    PendingRegistrationDB,
    TenantDB,
    UserDB,
)
from api.main import app
from api.security.jwt import create_access_token, decode_and_verify_access_token
from api.security.passwords import hash_password
from api.services.email_service import EmailService
from api.services.rate_limiter import RateLimiter, RateLimiterUnavailableError
from api.services.registration_service import RegistrationService, hash_token
from api.services.session_service import SessionService, hash_refresh_token


@pytest.fixture(autouse=True)
def ensure_postgresql_environment():
    """Ensure PostgreSQL settings are active for session tests."""
    import os
    from dotenv import dotenv_values
    from src.env_loader import _project_root

    root = _project_root()
    deploy_env = root / "deploy" / ".env"
    if deploy_env.is_file():
        vals = dotenv_values(str(deploy_env))
        if "GID_API_DATABASE_URL" in vals and vals["GID_API_DATABASE_URL"]:
            os.environ["GID_API_DATABASE_URL"] = vals["GID_API_DATABASE_URL"]
    get_api_settings.cache_clear()
    yield
    if deploy_env.is_file():
        vals = dotenv_values(str(deploy_env))
        if "GID_API_DATABASE_URL" in vals and vals["GID_API_DATABASE_URL"]:
            os.environ["GID_API_DATABASE_URL"] = vals["GID_API_DATABASE_URL"]
    get_api_settings.cache_clear()


@pytest.fixture
def client():
    """Test client with isolated IP per test to avoid rate-limiter bleed."""
    unique_ip = f"10.30.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
    with TestClient(app, headers={"X-Forwarded-For": unique_ip}) as test_client:
        yield test_client


async def _create_test_tenant_and_user(
    email: str, password: str, tenant_slug: str
) -> tuple[str, str]:
    """Helper to register and verify a permanent tenant and user directly."""
    settings = get_api_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            # Check if exists
            res = await conn.execute(
                select(UserDB.id).where(UserDB.email == email.lower())
            )
            if res.scalar_one_or_none():
                return "", ""

            tenant_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            pwd_hash = hash_password(password)

            await conn.execute(
                TenantDB.__table__.insert().values(
                    id=tenant_id,
                    name=f"Org {tenant_slug}",
                    slug=tenant_slug,
                    tier="basic",
                    settings={},
                    is_active=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )

            await conn.execute(
                UserDB.__table__.insert().values(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=email.lower(),
                    password_hash=pwd_hash,
                    role="owner",
                    is_active=True,
                    display_name=f"User {tenant_slug}",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            return tenant_id, user_id
    finally:
        await engine.dispose()


# ==============================================================================
# 1. Login & Session Creation Tests
# ==============================================================================


def test_login_issues_session_and_refresh_token(client: TestClient):
    """Login issues access token, rotating opaque refresh token, and persistent session ID."""
    test_id = uuid.uuid4().hex[:6]
    test_email = f"session.user.{test_id}@example.com"
    test_slug = f"session-org-{test_id}"
    password = "SecurePassword123!"

    asyncio.run(_create_test_tenant_and_user(test_email, password, test_slug))

    login_resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": test_email,
            "password": password,
            "tenant_slug": test_slug,
        },
    )
    assert login_resp.status_code == status.HTTP_200_OK
    data = login_resp.json()

    assert "access_token" in data
    assert "refresh_token" in data
    assert "session_id" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900

    refresh_token = data["refresh_token"]
    session_id = data["session_id"]
    access_token = data["access_token"]

    # Verify refresh token entropy (> 256 bits; 48 urlsafe chars is ~384 bits)
    assert len(refresh_token) >= 48

    # Verify access token JWT payload contains sid matching session_id
    payload = decode_and_verify_access_token(access_token)
    assert payload["sid"] == session_id
    assert payload["sub"]
    assert payload["tid"]


# ==============================================================================
# 2. Refresh Token Rotation & Replay Detection Tests
# ==============================================================================


def test_refresh_rotates_credentials_successfully(client: TestClient):
    """POST /refresh rotates refresh token, increments counter, and issues fresh access token."""
    test_id = uuid.uuid4().hex[:6]
    test_email = f"rotate.user.{test_id}@example.com"
    test_slug = f"rotate-org-{test_id}"
    password = "SecurePassword123!"

    asyncio.run(_create_test_tenant_and_user(test_email, password, test_slug))

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": password, "tenant_slug": test_slug},
    )
    assert login_resp.status_code == 200
    token1 = login_resp.json()["access_token"]
    refresh1 = login_resp.json()["refresh_token"]
    session_id1 = login_resp.json()["session_id"]

    # Perform rotation
    refresh_resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh1},
    )
    assert refresh_resp.status_code == status.HTTP_200_OK
    data = refresh_resp.json()

    token2 = data["access_token"]
    refresh2 = data["refresh_token"]
    session_id2 = data["session_id"]

    # Session ID remains same, tokens are rotated
    assert session_id2 == session_id1
    assert refresh2 != refresh1
    assert token2 != token1

    # Old access token can still access /me until expiry, new token can also access /me
    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == test_email


def test_consumed_token_replay_attack_revokes_token_family(client: TestClient):
    """Replaying an already-consumed refresh token immediately revokes the session family."""
    test_id = uuid.uuid4().hex[:6]
    test_email = f"replay.user.{test_id}@example.com"
    test_slug = f"replay-org-{test_id}"
    password = "SecurePassword123!"

    asyncio.run(_create_test_tenant_and_user(test_email, password, test_slug))

    # 1. Login to obtain refresh1
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": password, "tenant_slug": test_slug},
    )
    refresh1 = login_resp.json()["refresh_token"]

    # 2. Legitimate rotation: refresh1 consumed, refresh2 issued
    ref_resp1 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert ref_resp1.status_code == 200
    refresh2 = ref_resp1.json()["refresh_token"]
    token2 = ref_resp1.json()["access_token"]

    # 3. Malicious / replayed attempt: Present refresh1 AGAIN
    replay_resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert replay_resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert "token reuse detected" in replay_resp.json()["detail"].lower()

    # 4. Now verify that refresh2 has ALSO been revoked as part of the compromised family
    second_refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh2})
    assert second_refresh.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in second_refresh.json()["detail"].lower()

    # 5. Access token associated with this revoked session must now FAIL
    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert me_resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in me_resp.json()["detail"].lower()


def test_expired_refresh_token_rejected(client: TestClient):
    """Expired refresh tokens return 401 Unauthorized."""
    test_id = uuid.uuid4().hex[:6]
    test_email = f"expired.user.{test_id}@example.com"
    test_slug = f"expired-org-{test_id}"
    password = "SecurePassword123!"

    asyncio.run(_create_test_tenant_and_user(test_email, password, test_slug))

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": password, "tenant_slug": test_slug},
    )
    refresh_token = login_resp.json()["refresh_token"]
    session_id = login_resp.json()["session_id"]

    # Manually expire the session in database
    async def _expire_session():
        settings = get_api_settings()
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    AuthSessionDB.__table__.update()
                    .where(AuthSessionDB.id == session_id)
                    .values(expires_at=datetime.utcnow() - timedelta(days=1))
                )
        finally:
            await engine.dispose()

    asyncio.run(_expire_session())

    ref_resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert ref_resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert "expired" in ref_resp.json()["detail"].lower()


def test_revoked_session_refresh_rejected(client: TestClient):
    """Explicitly revoked sessions reject refresh attempts."""
    test_id = uuid.uuid4().hex[:6]
    test_email = f"revoked.user.{test_id}@example.com"
    test_slug = f"revoked-org-{test_id}"
    password = "SecurePassword123!"

    asyncio.run(_create_test_tenant_and_user(test_email, password, test_slug))

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": password, "tenant_slug": test_slug},
    )
    refresh_token = login_resp.json()["refresh_token"]
    session_id = login_resp.json()["session_id"]

    # Mark session revoked in DB
    async def _revoke_session():
        settings = get_api_settings()
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    AuthSessionDB.__table__.update()
                    .where(AuthSessionDB.id == session_id)
                    .values(is_revoked=True, revoked_at=datetime.utcnow(), revocation_reason="admin")
                )
        finally:
            await engine.dispose()

    asyncio.run(_revoke_session())

    ref_resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert ref_resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in ref_resp.json()["detail"].lower()


# ==============================================================================
# 3. Logout & Immediate Access-Token Revocation Tests
# ==============================================================================


def test_logout_current_session_revokes_access_and_refresh_tokens(client: TestClient):
    """Logout invalidates both the refresh token and all associated access tokens immediately."""
    test_id = uuid.uuid4().hex[:6]
    test_email = f"logout.user.{test_id}@example.com"
    test_slug = f"logout-org-{test_id}"
    password = "SecurePassword123!"

    asyncio.run(_create_test_tenant_and_user(test_email, password, test_slug))

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": password, "tenant_slug": test_slug},
    )
    access_token = login_resp.json()["access_token"]
    refresh_token = login_resp.json()["refresh_token"]

    # 1. Access token works initially
    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_resp.status_code == 200

    # 2. Call /logout
    logout_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_resp.status_code == status.HTTP_200_OK
    assert "logged out" in logout_resp.json()["message"].lower()

    # 3. Access token MUST FAIL IMMEDIATELY (no waiting for expiry!)
    me_after = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_after.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in me_after.json()["detail"].lower()

    # 4. Refresh token MUST ALSO FAIL
    ref_after = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert ref_after.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in ref_after.json()["detail"].lower()


def test_logout_all_sessions(client: TestClient):
    """Logging out with all_sessions=True revokes all active sessions for the user."""
    test_id = uuid.uuid4().hex[:6]
    test_email = f"logoutall.user.{test_id}@example.com"
    test_slug = f"logoutall-org-{test_id}"
    password = "SecurePassword123!"

    asyncio.run(_create_test_tenant_and_user(test_email, password, test_slug))

    # Login 1 (Desktop)
    resp1 = client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": password, "tenant_slug": test_slug},
    )
    token1 = resp1.json()["access_token"]

    # Login 2 (Mobile / Browser)
    resp2 = client.post(
        "/api/v1/auth/login",
        json={"email": test_email, "password": password, "tenant_slug": test_slug},
    )
    token2 = resp2.json()["access_token"]
    assert token1 != token2

    # Logout all sessions using token2
    logout_resp = client.post(
        "/api/v1/auth/logout",
        json={"all_sessions": True},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["revoked_sessions"] >= 2

    # Both tokens are now rejected
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token1}"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token2}"}).status_code == 401


def test_forged_session_identifier_fails(client: TestClient):
    """Forged session identifier in a signed access token fails closed with 401."""
    test_id = uuid.uuid4().hex[:6]
    test_email = f"forged.user.{test_id}@example.com"
    test_slug = f"forged-org-{test_id}"
    password = "SecurePassword123!"

    tenant_id, user_id = asyncio.run(_create_test_tenant_and_user(test_email, password, test_slug))

    # Create signed token with non-existent session_id
    fake_session_id = str(uuid.uuid4())
    token, _ = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=fake_session_id,
    )

    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert "not found or has been revoked" in resp.json()["detail"].lower()


# ==============================================================================
# 4. Redis Fail-Closed & Concurrency Hardening Tests
# ==============================================================================


def test_redis_unavailable_fail_closed(client: TestClient):
    """Public endpoints fail closed with HTTP 503 when Redis rate limiter is unavailable."""
    with patch.object(
        RateLimiter,
        "check_login_ip",
        side_effect=RateLimiterUnavailableError("Redis down"),
    ):
        res_login = client.post(
            "/api/v1/auth/login",
            json={"email": "any@example.com", "password": "AnyPassword123!"},
        )
        assert res_login.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "temporarily unavailable" in res_login.json()["detail"].lower()

    with patch.object(
        RateLimiter,
        "check_registration_ip",
        side_effect=RateLimiterUnavailableError("Redis down"),
    ):
        res_reg = client.post(
            "/api/v1/auth/register",
            json={
                "email": "any@example.com",
                "password": "AnyPassword123!",
                "tenant_name": "Any Org",
            },
        )
        assert res_reg.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    with patch.object(
        RateLimiter,
        "check_resend_cooldown",
        side_effect=RateLimiterUnavailableError("Redis down"),
    ):
        res_resend = client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "any@example.com"},
        )
        assert res_resend.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_concurrent_registration_token_verification_single_use(client: TestClient):
    """Concurrent attempts to verify the same registration token create exactly one tenant and owner."""
    test_email = f"concurrent_{uuid.uuid4().hex[:8]}@example.com"
    test_slug = f"concurrent-org-{uuid.uuid4().hex[:6]}"
    password = "SecurePassword123!"
    captured_token = None

    async def fake_send_email(**kwargs):
        nonlocal captured_token
        captured_token = kwargs["raw_token"]
        return True

    with patch.object(EmailService, "send_verification_email", side_effect=fake_send_email):
        reg_res = client.post(
            "/api/v1/auth/register",
            json={
                "email": test_email,
                "password": password,
                "tenant_name": "Concurrent Org",
                "tenant_slug": test_slug,
            },
        )
        assert reg_res.status_code == status.HTTP_202_ACCEPTED

    assert captured_token is not None

    # Simulate two simultaneous calls with the exact same token
    res1 = client.post("/api/v1/auth/verify-email", json={"token": captured_token})
    res2 = client.post("/api/v1/auth/verify-email", json={"token": captured_token})

    statuses = [res1.status_code, res2.status_code]
    assert 200 in statuses
    assert 400 in statuses
    # Exactly one succeeded and one failed
    assert statuses.count(200) == 1
    assert statuses.count(400) == 1
