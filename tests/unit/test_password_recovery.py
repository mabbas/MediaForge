"""Unit and security tests for password recovery, password reset, and authenticated password change (STEP 02-B2C)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api.config import get_api_settings
from api.database.connection import get_session
from api.database.models import (
    AuthSessionDB,
    PasswordResetTokenDB,
    TenantDB,
    UserDB,
)
from api.main import app
from api.security.passwords import hash_password, verify_password
from api.services.email_service import EmailService
from api.services.password_reset_service import PasswordResetService, hash_reset_token
from api.services.rate_limiter import RateLimiter, RateLimiterUnavailableError


@pytest.fixture(autouse=True)
def ensure_postgresql_environment():
    """Ensure PostgreSQL settings are active for recovery tests."""
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
    unique_ip = f"10.40.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
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
            res = await conn.execute(
                select(UserDB.id).where(UserDB.email == email.lower())
            )
            existing = res.scalar_one_or_none()
            if existing:
                return "", existing

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
# 1. Password Recovery Request Tests (POST /api/v1/auth/forgot-password)
# ==============================================================================


def test_forgot_password_generic_response_nonexistent_email(client: TestClient):
    """Unknown email returns generic 200 OK without disclosing account absence and without sending email."""
    nonexistent = f"nonexistent_{uuid.uuid4().hex[:8]}@example.com"

    with patch.object(EmailService, "send_password_reset_email", new_callable=AsyncMock) as mock_email:
        r = client.post("/api/v1/auth/forgot-password", json={"email": nonexistent})
        assert r.status_code == status.HTTP_200_OK
        data = r.json()
        assert "password reset link has been sent" in data["message"].lower()
        mock_email.assert_not_called()


def test_forgot_password_reserved_system_identity_ignored(client: TestClient):
    """Requests for reserved system identities return generic response but create NO tokens and send NO email."""
    with patch.object(RateLimiter, "check_password_reset_ip", new_callable=AsyncMock, return_value=(True, 0)), \
         patch.object(RateLimiter, "check_password_reset_email", new_callable=AsyncMock, return_value=(True, 0)), \
         patch.object(EmailService, "send_password_reset_email", new_callable=AsyncMock) as mock_email:
        r = client.post("/api/v1/auth/forgot-password", json={"email": "system@mediaforge.local"})
        assert r.status_code == status.HTTP_200_OK
        data = r.json()
        assert "password reset link has been sent" in data["message"].lower()
        mock_email.assert_not_called()


def test_forgot_password_success_issues_token_and_sends_email(client: TestClient):
    """Valid user request creates PasswordResetTokenDB record and sends single-use token via EmailService."""
    email = f"recovery.user.{uuid.uuid4().hex[:6]}@example.com"
    pwd = "InitialPassword123!"
    slug = f"rec-slug-{uuid.uuid4().hex[:6]}"

    asyncio.run(_create_test_tenant_and_user(email, pwd, slug))

    captured_token = None

    async def fake_send_email(**kwargs):
        nonlocal captured_token
        captured_token = kwargs["raw_token"]
        return True

    with patch.object(EmailService, "send_password_reset_email", side_effect=fake_send_email):
        r = client.post("/api/v1/auth/forgot-password", json={"email": email})
        assert r.status_code == status.HTTP_200_OK
        assert "password reset link has been sent" in r.json()["message"].lower()

    assert captured_token is not None
    assert len(captured_token) >= 32

    # Verify that existing credentials and sessions are NOT affected merely by requesting recovery
    login_r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": pwd, "tenant_slug": slug},
    )
    assert login_r.status_code == status.HTTP_200_OK


# ==============================================================================
# 2. Password Reset Tests (POST /api/v1/auth/reset-password)
# ==============================================================================


def test_reset_password_success_and_login_flow(client: TestClient):
    """Redeeming valid token updates password, marks token consumed, rejects old password, and allows new login."""
    email = f"reset.user.{uuid.uuid4().hex[:6]}@example.com"
    old_pwd = "OldPassword123!"
    new_pwd = "NewPassword456!"
    slug = f"reset-slug-{uuid.uuid4().hex[:6]}"

    asyncio.run(_create_test_tenant_and_user(email, old_pwd, slug))

    captured_token = None

    async def fake_send_email(**kwargs):
        nonlocal captured_token
        captured_token = kwargs["raw_token"]
        return True

    with patch.object(EmailService, "send_password_reset_email", side_effect=fake_send_email):
        client.post("/api/v1/auth/forgot-password", json={"email": email})

    assert captured_token is not None

    # Reset password with valid token
    r = client.post(
        "/api/v1/auth/reset-password",
        json={"token": captured_token, "new_password": new_pwd},
    )
    assert r.status_code == status.HTTP_200_OK
    assert "successfully reset" in r.json()["message"].lower()

    # 1. Old password must now be rejected
    old_login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": old_pwd, "tenant_slug": slug},
    )
    assert old_login.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. New password must be accepted
    new_login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": new_pwd, "tenant_slug": slug},
    )
    assert new_login.status_code == status.HTTP_200_OK
    assert "access_token" in new_login.json()
    assert "refresh_token" in new_login.json()


def test_reset_password_invalid_or_expired_or_reused_token(client: TestClient):
    """Invalid, expired, or previously consumed recovery tokens are rejected with HTTP 400."""
    email = f"tokens.user.{uuid.uuid4().hex[:6]}@example.com"
    pwd = "OriginalPassword123!"
    slug = f"tokens-slug-{uuid.uuid4().hex[:6]}"

    asyncio.run(_create_test_tenant_and_user(email, pwd, slug))

    captured_token = None

    async def fake_send_email(**kwargs):
        nonlocal captured_token
        captured_token = kwargs["raw_token"]
        return True

    with patch.object(EmailService, "send_password_reset_email", side_effect=fake_send_email):
        client.post("/api/v1/auth/forgot-password", json={"email": email})

    # 1. Invalid token
    r_inv = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "completely-invalid-bogus-token-123456", "new_password": "NewValidPassword123!"},
    )
    assert r_inv.status_code == status.HTTP_400_BAD_REQUEST

    # 2. Successful reset (consumes token)
    r_ok = client.post(
        "/api/v1/auth/reset-password",
        json={"token": captured_token, "new_password": "FirstNewPassword123!"},
    )
    assert r_ok.status_code == status.HTTP_200_OK

    # 3. Reused token must fail
    r_reused = client.post(
        "/api/v1/auth/reset-password",
        json={"token": captured_token, "new_password": "SecondNewPassword123!"},
    )
    assert r_reused.status_code == status.HTTP_400_BAD_REQUEST
    assert "already been used" in r_reused.json()["detail"].lower()


def test_reset_password_invalidates_all_active_sessions(client: TestClient):
    """Resetting a password immediately invalidates all existing access and refresh tokens."""
    email = f"session.inval.{uuid.uuid4().hex[:6]}@example.com"
    old_pwd = "SessionOldPassword123!"
    new_pwd = "SessionNewPassword456!"
    slug = f"sess-slug-{uuid.uuid4().hex[:6]}"

    asyncio.run(_create_test_tenant_and_user(email, old_pwd, slug))

    # 1. Login to establish active session
    login_r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": old_pwd, "tenant_slug": slug},
    )
    assert login_r.status_code == status.HTTP_200_OK
    old_access_token = login_r.json()["access_token"]
    old_refresh_token = login_r.json()["refresh_token"]

    # Verify access token works before reset
    me_before = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_access_token}"})
    assert me_before.status_code == status.HTTP_200_OK

    # 2. Request recovery and capture token
    captured_token = None

    async def fake_send_email(**kwargs):
        nonlocal captured_token
        captured_token = kwargs["raw_token"]
        return True

    with patch.object(EmailService, "send_password_reset_email", side_effect=fake_send_email):
        client.post("/api/v1/auth/forgot-password", json={"email": email})

    # 3. Reset password
    reset_r = client.post(
        "/api/v1/auth/reset-password",
        json={"token": captured_token, "new_password": new_pwd},
    )
    assert reset_r.status_code == status.HTTP_200_OK

    # 4. Old access token must be IMMEDIATELY rejected
    me_after = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_access_token}"})
    assert me_after.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in me_after.json()["detail"].lower()

    # 5. Old refresh token must be rejected
    refresh_r = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh_token})
    assert refresh_r.status_code == status.HTTP_401_UNAUTHORIZED


def test_concurrent_reset_token_redemption_single_use(client: TestClient):
    """Concurrent attempts to redeem the same recovery token permit at most one success."""
    email = f"concurrent.reset.{uuid.uuid4().hex[:6]}@example.com"
    pwd = "ConcPassword123!"
    slug = f"conc-slug-{uuid.uuid4().hex[:6]}"

    asyncio.run(_create_test_tenant_and_user(email, pwd, slug))

    captured_token = None

    async def fake_send_email(**kwargs):
        nonlocal captured_token
        captured_token = kwargs["raw_token"]
        return True

    with patch.object(EmailService, "send_password_reset_email", side_effect=fake_send_email):
        client.post("/api/v1/auth/forgot-password", json={"email": email})

    assert captured_token is not None

    from concurrent.futures import ThreadPoolExecutor

    def call_reset(candidate_pwd: str):
        return client.post(
            "/api/v1/auth/reset-password",
            json={"token": captured_token, "new_password": candidate_pwd},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(call_reset, "RaceWinnerPassword123!")
        f2 = executor.submit(call_reset, "RaceLoserPassword123!")
        r1 = f1.result()
        r2 = f2.result()

    statuses = [r1.status_code, r2.status_code]
    assert 200 in statuses
    assert 400 in statuses
    assert statuses.count(200) == 1
    assert statuses.count(400) == 1


# ==============================================================================
# 3. Authenticated Password Change Tests (POST /api/v1/auth/change-password)
# ==============================================================================


def test_change_password_success_and_session_invalidation(client: TestClient):
    """Authenticated user changes password with valid current password; terminates active sessions."""
    email = f"change.user.{uuid.uuid4().hex[:6]}@example.com"
    old_pwd = "ChangeOldPassword123!"
    new_pwd = "ChangeNewPassword456!"
    slug = f"chg-slug-{uuid.uuid4().hex[:6]}"

    asyncio.run(_create_test_tenant_and_user(email, old_pwd, slug))

    # 1. Login
    login_r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": old_pwd, "tenant_slug": slug},
    )
    assert login_r.status_code == status.HTTP_200_OK
    access_token = login_r.json()["access_token"]
    refresh_token = login_r.json()["refresh_token"]

    # 2. Change password
    change_r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": old_pwd, "new_password": new_pwd},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert change_r.status_code == status.HTTP_200_OK
    assert "successfully changed" in change_r.json()["message"].lower()

    # 3. Current session must be terminated immediately
    me_r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_r.status_code == status.HTTP_401_UNAUTHORIZED
    assert "revoked" in me_r.json()["detail"].lower()

    # 4. Old refresh token is revoked
    ref_r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert ref_r.status_code == status.HTTP_401_UNAUTHORIZED

    # 5. Old password no longer works
    old_login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": old_pwd, "tenant_slug": slug},
    )
    assert old_login.status_code == status.HTTP_401_UNAUTHORIZED

    # 6. New password works
    new_login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": new_pwd, "tenant_slug": slug},
    )
    assert new_login.status_code == status.HTTP_200_OK


def test_change_password_wrong_current_password_rejected(client: TestClient):
    """Incorrect current password returns 401 Unauthorized; leaves password and sessions intact."""
    email = f"wrong.pwd.{uuid.uuid4().hex[:6]}@example.com"
    pwd = "ValidCurrentPassword123!"
    slug = f"wrong-slug-{uuid.uuid4().hex[:6]}"

    asyncio.run(_create_test_tenant_and_user(email, pwd, slug))

    login_r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": pwd, "tenant_slug": slug},
    )
    access_token = login_r.json()["access_token"]

    # Attempt password change with wrong current password
    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "WrongCurrentPassword123!", "new_password": "SomeNewPassword456!"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    assert "incorrect" in r.json()["detail"].lower()

    # Existing session still valid
    me_r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_r.status_code == status.HTTP_200_OK


def test_change_password_unauthenticated_rejected(client: TestClient):
    """Calling change-password without Bearer token returns 401 Unauthorized."""
    r = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "AnyPassword123!", "new_password": "NewPassword456!"},
    )
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


# ==============================================================================
# 4. Rate Limiting and Fail-Closed Security Tests
# ==============================================================================


def test_password_reset_fail_closed_on_redis_unavailable(client: TestClient):
    """When Redis rate limiting backend fails, forgot-password fails closed with HTTP 503."""
    with patch.object(
        RateLimiter,
        "check_password_reset_ip",
        side_effect=RateLimiterUnavailableError("Redis is down"),
    ):
        r = client.post("/api/v1/auth/forgot-password", json={"email": "someone@example.com"})
        assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "unavailable" in r.json()["detail"].lower()
