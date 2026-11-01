"""FastAPI dependency injection."""

from __future__ import annotations

from functools import lru_cache

from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request

from src.grabitdown import GrabItDown

_app_instance: GrabItDown | None = None


def get_app() -> GrabItDown:
    """Get the GrabItDown app instance (singleton)."""
    global _app_instance
    if _app_instance is None:
        _app_instance = GrabItDown()
        _app_instance.start()

        # Wire progress bridge to engine
        from api.services.progress_bridge import get_progress_bridge

        bridge = get_progress_bridge()
        bridge.set_app(_app_instance)
        _app_instance._engine.progress_tracker.add_listener(bridge.on_progress)

        # Wire DB sync to engine
        from api.services.engine_sync import get_engine_sync

        db_sync = get_engine_sync()
        db_sync.set_engine(_app_instance._engine)
        _app_instance._engine.progress_tracker.add_listener(db_sync.on_progress)

    return _app_instance


def get_registry():
    return get_app()._registry


def get_engine():
    return get_app()._engine


def get_feature_gate():
    return get_app()._gate


def get_usage_tracker():
    return get_app()._usage


async def shutdown_app() -> None:
    """Shut down the GrabItDown app singleton."""
    global _app_instance
    if _app_instance:
        _app_instance.shutdown(wait=True)
        _app_instance = None


# ── Auth Dependencies (Typed Security & Tenant Context) ──

from enum import Enum
from pydantic import BaseModel, Field
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


class AuthMethod(str, Enum):
    """Supported authentication mechanisms."""

    JWT = "jwt"
    API_KEY = "api_key"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


class AuthenticatedPrincipal(BaseModel):
    """Typed, verified security context representing an authenticated principal.

    Must ONLY be created after genuine credential verification.
    Client-supplied identity headers (e.g. X-User-ID, X-Tenant-ID) are strictly rejected.
    """

    user_id: str
    tenant_id: str
    session_id: str | None = None
    email: str | None = None
    role: str = "member"  # "owner", "admin", "member"
    tier: str = "basic"  # "basic", "pro", "platinum"
    auth_method: AuthMethod = AuthMethod.JWT
    api_key_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    is_system_owner: bool = False

    model_config = {"frozen": True}

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def is_admin_or_owner(self) -> bool:
        return self.role in ("admin", "owner", "system", "system_admin") or self.is_system_owner

    @property
    def is_system_admin(self) -> bool:
        return self.role in ("system", "system_admin") or self.is_system_owner


class CurrentUser(AuthenticatedPrincipal):
    """Backwards-compatible alias for AuthenticatedPrincipal."""

    def __init__(
        self,
        user_id: str = "system",
        tenant_id: str = "default",
        session_id: str | None = None,
        email: str | None = None,
        role: str = "owner",
        tier: str = "platinum",
        auth_method: AuthMethod = AuthMethod.JWT,
        api_key_id: str | None = None,
        permissions: list[str] | None = None,
        is_system_owner: bool = False,
        **data,
    ):
        if permissions is None:
            permissions = []
        super().__init__(
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            email=email,
            role=role,
            tier=tier,
            auth_method=auth_method,
            api_key_id=api_key_id,
            permissions=permissions,
            is_system_owner=is_system_owner,
            **data,
        )


class TenantContext(BaseModel):
    """Tenant isolation context derived strictly from a verified principal.

    Guarantees that tenant identity cannot be spoofed via client request parameters.
    """

    tenant_id: str
    principal: AuthenticatedPrincipal

    model_config = {"frozen": True}

    def check_tenant_access(self, target_tenant_id: str) -> bool:
        """Enforce strict tenant boundary: deny access to any foreign tenant's resources."""
        if self.principal.is_system_admin or self.principal.is_system_owner:
            return True
        if self.tenant_id != target_tenant_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: principal is not authorized for target tenant resources",
            )
        return True


from sqlalchemy.ext.asyncio import AsyncSession
from api.database.connection import get_session

security_scheme = HTTPBearer(auto_error=False)


async def resolve_principal_from_token(
    token: str,
    session: AsyncSession,
) -> CurrentUser:
    """Cryptographically verify a JWT and resolve authoritative database identity."""
    from api.database.models import TenantDB, UserDB
    from api.security.jwt import (
        TokenAlgorithmError,
        TokenExpiredError,
        TokenInvalidError,
        decode_and_verify_access_token,
    )

    try:
        payload = decode_and_verify_access_token(token)
    except TokenExpiredError:
        raise HTTPException(
            status_code=401,
            detail="Token signature has expired",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\", error_description=\"The token has expired\""},
        )
    except (TokenAlgorithmError, TokenInvalidError):
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
        )

    user_id = payload.get("sub")
    token_tenant_id = payload.get("tid")
    if not user_id or not token_tenant_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Resolve authoritative user record from PostgreSQL
    user = await session.get(UserDB, str(user_id))
    if not user:
        raise HTTPException(
            status_code=401,
            detail="User account not found or has been removed",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Authoritative tenant membership check
    if user.tenant_id != token_tenant_id:
        raise HTTPException(
            status_code=401,
            detail="Token tenant claims mismatch user tenant membership",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tenant = await session.get(TenantDB, user.tenant_id)
    if not tenant or not tenant.is_active:
        raise HTTPException(
            status_code=401,
            detail="Tenant account is inactive or not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate persistent session if sid claim is present (STEP 02-B2B)
    session_id = payload.get("sid")
    if session_id:
        from datetime import datetime
        from api.database.models import AuthSessionDB

        auth_session = await session.get(AuthSessionDB, str(session_id))
        if not auth_session:
            raise HTTPException(
                status_code=401,
                detail="Session not found or has been revoked",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        if auth_session.is_revoked:
            raise HTTPException(
                status_code=401,
                detail="Session has been revoked",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        if auth_session.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(
                status_code=401,
                detail="Session has expired",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        if auth_session.user_id != user.id or auth_session.tenant_id != user.tenant_id:
            raise HTTPException(
                status_code=401,
                detail="Session user/tenant mismatch",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

    effective_tier = user.tier_override or tenant.tier or "basic"
    is_sys_admin_or_owner = (
        (user.tenant_id == "00000000-0000-0000-0000-000000000001" and user.role in ("owner", "system", "system_admin"))
        or (user.role in ("system", "system_admin"))
    )

    return CurrentUser(
        user_id=user.id,
        tenant_id=user.tenant_id,
        session_id=str(session_id) if session_id else None,
        email=user.email,
        role=user.role or "member",
        tier=effective_tier,
        auth_method=AuthMethod.JWT,
        is_system_owner=is_sys_admin_or_owner,
    )


async def get_current_user(
    request: Request,
    auth: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """Resolve and validate the authenticated principal.

    Fail-closed security boundary:
    - Supports JWT (Bearer <token>) and commercial API keys (X-API-Key: <key>).
    - Ambiguous requests presenting both JWT and API key strictly receive HTTP 400.
    - Anonymous or uncredentialed requests receive HTTP 401.
    - Invalid, inactive, expired, or revoked credentials receive HTTP 401.
    - Authoritative user, tenant, and API key states are verified against PostgreSQL.
    - Dual rate limits (per-key and tenant aggregate) are enforced with fail-closed HTTP 503 on Redis outage.
    - Key usage telemetry is captured without storing secret keys.
    """
    auth_header = request.headers.get("authorization")
    api_key_header = request.headers.get("x-api-key")

    # Reject ambiguous requests containing both JWT and API-key credentials (Rule 8)
    if auth_header and api_key_header:
        raise HTTPException(
            status_code=400,
            detail="Ambiguous authentication credentials: both Bearer token and X-API-Key header provided.",
        )

    # API Key Authentication Path
    if api_key_header is not None:
        raw_key = api_key_header.strip()
        if not raw_key:
            raise HTTPException(
                status_code=401,
                detail="API key cannot be empty",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        from api.services.api_key_service import APIKeyService
        from api.services.rate_limiter import RateLimiter, RateLimiterUnavailableError

        resolved = await APIKeyService.resolve_key_by_secret(session, raw_key)
        if not resolved:
            raise HTTPException(
                status_code=401,
                detail="Invalid or unrecognized API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        key, tenant = resolved
        if key.is_revoked:
            raise HTTPException(
                status_code=401,
                detail="API key has been revoked",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        if not key.is_active:
            raise HTTPException(
                status_code=401,
                detail="API key is inactive",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        if key.expires_at and key.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(
                status_code=401,
                detail="API key has expired",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        if not tenant.is_active:
            raise HTTPException(
                status_code=401,
                detail="Tenant account is inactive or not found",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        # Dual rate limit check (individual key & tenant aggregate)
        try:
            allowed, reason, retry_after = await RateLimiter.check_api_key_limits(
                key_id=key.id,
                tenant_id=tenant.id,
                key_limit=key.rate_limit,
            )
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=reason,
                    headers={"Retry-After": str(retry_after)},
                )
        except RateLimiterUnavailableError:
            raise HTTPException(
                status_code=503,
                detail="Rate limiting service unavailable. Please retry later.",
            )

        # Record usage telemetry in Redis and update DB metadata
        await RateLimiter.record_api_key_usage(key.id, tenant.id, operation="request")
        key.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        key.total_requests = (key.total_requests or 0) + 1
        await session.commit()

        return CurrentUser(
            user_id=f"service:{key.id}",
            tenant_id=tenant.id,
            session_id=None,
            email=None,
            role="service_account",
            tier=tenant.tier or "basic",
            auth_method=AuthMethod.API_KEY,
            api_key_id=key.id,
            permissions=list(key.permissions or []),
            is_system_owner=False,
        )

    # JWT Authentication Path
    if auth is None or not auth.credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication credentials required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth.credentials.strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication credentials required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await resolve_principal_from_token(token, session)


async def get_tenant_context(
    principal: CurrentUser = Depends(get_current_user),
) -> TenantContext:
    """Resolve verified tenant context from authenticated principal."""
    return TenantContext(
        tenant_id=principal.tenant_id,
        principal=principal,
    )


def require_role(allowed_roles: list[str] | tuple[str, ...]):
    """Dependency factory enforcing role-based access control (RBAC)."""

    async def _role_checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.auth_method == AuthMethod.API_KEY:
            raise HTTPException(
                status_code=403,
                detail=f"Operation requires human user with role in: {', '.join(allowed_roles)}. API keys are not permitted.",
            )
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Operation requires one of the following roles: {', '.join(allowed_roles)}",
            )
        return user

    return _role_checker


def require_tenant_admin():
    """Dependency enforcing tenant administration privileges (admin or owner)."""
    return require_role(["admin", "owner", "system", "system_admin"])


def require_system_admin():
    """Dependency enforcing global system administration privileges (system or system_admin only)."""
    return require_role(["system", "system_admin"])


def require_scope(required_scope: str):
    """Dependency factory enforcing granular permission scope for API keys.

    For API keys: validates that required_scope is present in granted permissions.
    For JWT user sessions: passes through to role-based access control.
    """

    async def _scope_checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.auth_method == AuthMethod.API_KEY:
            # Service accounts cannot perform system or administrative operations
            if required_scope in ("system:admin", "config:write", "queue:manage"):
                raise HTTPException(
                    status_code=403,
                    detail="API keys are strictly forbidden from performing system-level operations.",
                )
            if required_scope not in user.permissions and "*" not in user.permissions:
                raise HTTPException(
                    status_code=403,
                    detail=f"Insufficient API key permissions. Required scope: '{required_scope}'",
                )
        return user

    return _scope_checker


def require_tier(min_tier: str):
    """Dependency factory for tier-based access control."""

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        tier_levels = {"basic": 0, "pro": 1, "platinum": 2}
        user_level = tier_levels.get(user.tier, 0)
        required_level = tier_levels.get(min_tier, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail=f"Requires {min_tier} tier or higher",
            )
        return user

    return _check
