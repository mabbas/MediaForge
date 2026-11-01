"""Unit tests for tenant registration, email verification, and rate limiting (STEP 02-B2A)."""

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
from api.database.models import PendingRegistrationDB, TenantDB, UserDB
from api.main import app
from api.schemas.registration import RegisterRequest, VerifyEmailRequest
from api.security.passwords import hash_password, verify_password
from api.services.email_service import EmailService
from api.services.rate_limiter import RateLimiter
from api.services.registration_service import RegistrationService, hash_token


@pytest.fixture(autouse=True)
def ensure_postgresql_environment():
    """Ensure PostgreSQL settings are active for registration tests."""
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
    """Test client for FastAPI app with isolated IP per test."""
    unique_ip = f"10.20.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
    with TestClient(app, headers={"X-Forwarded-For": unique_ip}) as test_client:
        yield test_client


# ==============================================================================
# 1. Schema Validation Tests
# ==============================================================================


def test_register_schema_validation_success():
    """Valid registration payload passes validation."""
    req = RegisterRequest(
        email="Alice@Example.COM",
        password="ValidPassword123!",
        tenant_name="Acme Corp",
        tenant_slug="acme-corp",
    )
    assert req.email == "alice@example.com"
    assert req.tenant_slug == "acme-corp"


def test_register_schema_weak_password_rejected():
    """Weak passwords lacking complexity are rejected."""
    # Too short
    with pytest.raises(ValueError, match="at least 8 characters"):
        RegisterRequest(
            email="test@example.com",
            password="Sh1!",
            tenant_name="Acme",
        )

    # Missing uppercase
    with pytest.raises(ValueError, match="uppercase"):
        RegisterRequest(
            email="test@example.com",
            password="lowercase123!",
            tenant_name="Acme",
        )

    # Missing digit
    with pytest.raises(ValueError, match="digit"):
        RegisterRequest(
            email="test@example.com",
            password="NoDigitsHere!",
            tenant_name="Acme",
        )

    # Missing special char
    with pytest.raises(ValueError, match="special character"):
        RegisterRequest(
            email="test@example.com",
            password="PasswordWithoutSpecial123",
            tenant_name="Acme",
        )


def test_register_schema_invalid_slug_rejected():
    """Slugs with invalid characters, uppercase, or leading/trailing hyphens are rejected."""
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        RegisterRequest(
            email="test@example.com",
            password="ValidPassword123!",
            tenant_name="Acme",
            tenant_slug="-invalid-start",
        )

    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        RegisterRequest(
            email="test@example.com",
            password="ValidPassword123!",
            tenant_name="Acme",
            tenant_slug="Invalid_Characters!",
        )


# ==============================================================================
# 2. Staged Registration Endpoint Tests
# ==============================================================================


def test_register_endpoint_staged_success(client: TestClient):
    """Registration stages record in pending table, sends email, and creates NO permanent entities."""
    test_email = f"staged_{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        "email": test_email,
        "password": "SecurePassword123!",
        "tenant_name": "Staged Org",
        "tenant_slug": f"staged-org-{uuid.uuid4().hex[:6]}",
    }

    with patch.object(EmailService, "send_verification_email", new_callable=AsyncMock) as mock_email:
        mock_email.return_value = True

        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert "Registration initiated" in data["message"]
        assert data["email"] == test_email.lower()
        assert "token" not in data
        assert "access_token" not in data

        # Verify email dispatch was triggered with raw token
        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args.kwargs
        assert call_kwargs["to_email"] == test_email.lower()
        raw_token = call_kwargs["raw_token"]
        assert len(raw_token) >= 32

    # Verify database state synchronously using test client or query
    # Login should FAIL because permanent user does not exist yet!
    login_res = client.post(
        "/api/v1/auth/login",
        json={
            "email": test_email,
            "password": "SecurePassword123!",
            "tenant_slug": payload["tenant_slug"],
        },
    )
    assert login_res.status_code == status.HTTP_401_UNAUTHORIZED


def test_register_endpoint_anti_enumeration(client: TestClient):
    """Registering an already existing permanent user returns generic response without error."""
    existing_email = f"existing_{uuid.uuid4().hex[:8]}@example.com"
    captured_token = None

    async def capture_token(**kwargs):
        nonlocal captured_token
        captured_token = kwargs["raw_token"]
        return True

    # 1. First register and verify this user so they exist permanently in UserDB
    with patch.object(EmailService, "send_verification_email", side_effect=capture_token):
        res1 = client.post(
            "/api/v1/auth/register",
            json={
                "email": existing_email,
                "password": "ValidPassword123!",
                "tenant_name": "First Tenant",
            },
        )
        assert res1.status_code == status.HTTP_202_ACCEPTED

    assert captured_token is not None
    res_verify = client.post("/api/v1/auth/verify-email", json={"token": captured_token})
    assert res_verify.status_code == status.HTTP_200_OK

    # 2. Now attempt to register again with the exact same email
    with patch.object(EmailService, "send_verification_email", new_callable=AsyncMock) as mock_email:
        res2 = client.post(
            "/api/v1/auth/register",
            json={
                "email": existing_email,
                "password": "AnotherPassword123!",
                "tenant_name": "Second Tenant",
            },
        )
        assert res2.status_code == status.HTTP_202_ACCEPTED
        assert "Registration initiated" in res2.json()["message"]
        # Email service must NOT send verification email for already existing permanent account
        mock_email.assert_not_called()


def test_register_endpoint_reserved_slug_rejected(client: TestClient):
    """Requesting a reserved slug returns 400 Bad Request."""
    payload = {
        "email": f"reserved_{uuid.uuid4().hex[:8]}@example.com",
        "password": "ValidPassword123!",
        "tenant_name": "Admin Tenant",
        "tenant_slug": "admin",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "reserved" in response.json()["detail"].lower()


# ==============================================================================
# 3. Verification & Account Activation Tests
# ==============================================================================


def test_verify_email_success_and_login_activation(client: TestClient):
    """Verifying with valid token creates basic tenant and owner user, allowing login."""
    test_email = f"verify_{uuid.uuid4().hex[:8]}@example.com"
    test_slug = f"verify-org-{uuid.uuid4().hex[:6]}"
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
                "tenant_name": "Verification Org",
                "tenant_slug": test_slug,
            },
        )
        assert reg_res.status_code == status.HTTP_202_ACCEPTED

    assert captured_token is not None

    # Now verify email using captured raw token
    verify_res = client.post(
        "/api/v1/auth/verify-email",
        json={"token": captured_token},
    )
    assert verify_res.status_code == status.HTTP_200_OK
    verify_data = verify_res.json()
    assert verify_data["email"] == test_email
    assert verify_data["tenant_slug"] == test_slug
    assert "user_id" in verify_data
    assert "tenant_id" in verify_data

    # Single-use: verifying a SECOND time must fail with 400
    verify_res2 = client.post(
        "/api/v1/auth/verify-email",
        json={"token": captured_token},
    )
    assert verify_res2.status_code == status.HTTP_400_BAD_REQUEST
    assert "invalid or expired" in verify_res2.json()["detail"].lower()

    # Now login with new credentials must SUCCEED!
    login_res = client.post(
        "/api/v1/auth/login",
        json={
            "email": test_email,
            "password": password,
            "tenant_slug": test_slug,
        },
    )
    assert login_res.status_code == status.HTTP_200_OK
    token_data = login_res.json()
    assert "access_token" in token_data
    jwt_token = token_data["access_token"]

    # Verify user profile via /me: tier must be "basic" and role must be "owner"
    me_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert me_res.status_code == status.HTTP_200_OK
    me_data = me_res.json()
    assert me_data["role"] == "owner"
    assert me_data["tier"] == "basic"
    assert me_data["tier"] != "platinum"


def test_verify_email_invalid_token(client: TestClient):
    """Invalid verification token returns 400 Bad Request."""
    res = client.post(
        "/api/v1/auth/verify-email",
        json={"token": "completely_invalid_token_1234567890"},
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "invalid or expired" in res.json()["detail"].lower()


# ==============================================================================
# 4. Resend Verification & Cooldown Tests
# ==============================================================================


def test_resend_verification_success(client: TestClient):
    """Resending verification updates token and sends new email."""
    test_email = f"resend_{uuid.uuid4().hex[:8]}@example.com"
    first_token = None
    second_token = None

    async def fake_send_first(**kwargs):
        nonlocal first_token
        first_token = kwargs["raw_token"]
        return True

    with patch.object(RateLimiter, "check_registration_ip", return_value=(True, 0)):
        with patch.object(EmailService, "send_verification_email", side_effect=fake_send_first):
            reg_res = client.post(
                "/api/v1/auth/register",
                json={
                    "email": test_email,
                    "password": "SecurePassword123!",
                    "tenant_name": f"Resend Org {uuid.uuid4().hex[:6]}",
                },
            )
            assert reg_res.status_code == status.HTTP_202_ACCEPTED

    async def fake_send_second(**kwargs):
        nonlocal second_token
        second_token = kwargs["raw_token"]
        return True

    # Bypass cooldown for test
    with patch.object(RateLimiter, "check_resend_cooldown", return_value=(True, 0)):
        with patch.object(EmailService, "send_verification_email", side_effect=fake_send_second):
            resend_res = client.post(
                "/api/v1/auth/resend-verification",
                json={"email": test_email},
            )
            assert resend_res.status_code == status.HTTP_200_OK
            assert "verification link has been sent" in resend_res.json()["message"]

    assert first_token is not None
    assert second_token is not None
    assert first_token != second_token

    # First token should now be invalidated; second token should succeed
    fail_res = client.post("/api/v1/auth/verify-email", json={"token": first_token})
    assert fail_res.status_code == status.HTTP_400_BAD_REQUEST

    succ_res = client.post("/api/v1/auth/verify-email", json={"token": second_token})
    assert succ_res.status_code == status.HTTP_200_OK


def test_resend_verification_cooldown_enforced(client: TestClient):
    """Repeated resend requests within cooldown period are rejected with 429."""
    with patch.object(RateLimiter, "check_resend_cooldown", return_value=(False, 45)):
        res = client.post(
            "/api/v1/auth/resend-verification",
            json={"email": "cooldown@example.com"},
        )
        assert res.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "45 seconds" in res.json()["detail"]
        assert res.headers.get("Retry-After") == "45"
