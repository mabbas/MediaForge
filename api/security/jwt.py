"""JWT access token issuance and verification (STEP 02-B1).

Enforces:
- Cryptographic signature validation with explicit allowed algorithm (HS256)
- Strict expiration (exp), issue time (iat), issuer (iss), audience (aud), and token type (typ) checks
- Short token lifetime (default 15 minutes)
- Rejection of alg="none", malformed, or untrusted tokens
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import jwt
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidAlgorithmError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    PyJWTError,
)

from api.config import get_api_settings

logger = logging.getLogger(__name__)


class JWTError(Exception):
    """Base exception for JWT validation failures."""


class TokenExpiredError(JWTError):
    """Token has expired."""


class TokenInvalidError(JWTError):
    """Token signature or structure is invalid."""


class TokenAlgorithmError(JWTError):
    """Disallowed algorithm or unsigned token."""


def _get_signing_key() -> str:
    settings = get_api_settings()
    key = settings.jwt_secret_key or settings.secret_key
    if not key or len(key) < 32:
        raise ValueError(
            "JWT secret key must be at least 32 characters long. Set GID_JWT_SECRET_KEY."
        )
    return key


def create_access_token(
    user_id: str,
    tenant_id: str,
    expires_in_seconds: int | None = None,
    extra_claims: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> tuple[str, int]:
    """Issue a signed JWT access token.

    Args:
        user_id: Authoritative user identifier (sub claim).
        tenant_id: Authoritative tenant identifier (tid claim).
        expires_in_seconds: Token lifetime in seconds (defaults to settings.access_token_expire_minutes * 60).
        extra_claims: Optional non-sensitive additional claims.
        session_id: Optional persistent session identifier (sid claim).

    Returns:
        tuple of (signed_jwt_string, expires_in_seconds).
    """
    settings = get_api_settings()
    secret_key = _get_signing_key()

    if expires_in_seconds is None:
        expires_in_seconds = settings.access_token_expire_minutes * 60

    now = int(time.time())
    exp = now + expires_in_seconds

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": exp,
        "jti": str(uuid.uuid4()),
        "typ": "access",
    }

    if session_id:
        payload["sid"] = str(session_id)

    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(
        payload,
        secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_in_seconds


def decode_and_verify_access_token(
    token: str,
    verify_aud: bool = True,
    verify_iss: bool = True,
) -> dict[str, Any]:
    """Decode and cryptographically verify a JWT access token.

    Validates:
    - Signature matching configured secret and algorithm
    - Token not expired (exp)
    - Valid issuer (iss) and audience (aud)
    - Token type == 'access'
    - Presence of required claims ('sub', 'tid', 'exp', 'iat', 'typ')

    Returns:
        Decoded payload dictionary.

    Raises:
        TokenExpiredError: If token expiration timestamp is in the past.
        TokenAlgorithmError: If algorithm is not the permitted algorithm.
        TokenInvalidError: If token signature, structure, or claims are invalid.
    """
    if not token or not isinstance(token, str):
        raise TokenInvalidError("Token must be a non-empty string")

    token = token.strip()
    if not token:
        raise TokenInvalidError("Token cannot be empty")

    settings = get_api_settings()
    secret_key = _get_signing_key()
    allowed_algorithms = [settings.jwt_algorithm]

    try:
        # First verify unverified headers to reject 'none' algorithm early
        unverified_headers = jwt.get_unverified_header(token)
        alg = unverified_headers.get("alg")
        if not alg or alg.lower() == "none" or alg != settings.jwt_algorithm:
            raise TokenAlgorithmError(f"Disallowed token algorithm: {alg}")

        payload = jwt.decode(
            token,
            secret_key,
            algorithms=allowed_algorithms,
            issuer=settings.jwt_issuer if verify_iss else None,
            audience=settings.jwt_audience if verify_aud else None,
            options={
                "require": ["exp", "iat", "sub", "tid", "typ"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": verify_iss,
                "verify_aud": verify_aud,
            },
        )

        # Enforce token type == "access"
        if payload.get("typ") != "access":
            raise TokenInvalidError(
                f"Invalid token type '{payload.get('typ')}', expected 'access'"
            )

        return payload

    except ExpiredSignatureError as exc:
        raise TokenExpiredError("Token signature has expired") from exc
    except InvalidSignatureError as exc:
        raise TokenInvalidError("Invalid token cryptographic signature") from exc
    except InvalidAudienceError as exc:
        raise TokenInvalidError("Invalid token audience claim") from exc
    except InvalidIssuerError as exc:
        raise TokenInvalidError("Invalid token issuer claim") from exc
    except InvalidAlgorithmError as exc:
        raise TokenAlgorithmError("Disallowed token algorithm") from exc
    except (DecodeError, PyJWTError) as exc:
        raise TokenInvalidError(f"Malformed or invalid token: {exc}") from exc
