"""Authentication endpoints: login, refresh, session management, registration, and verification (STEP 02-B1, STEP 02-B2A, STEP 02-B2B)."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.database.models import TenantDB, UserDB
from api.dependencies import CurrentUser, get_current_user
from api.schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
    TokenResponse,
    UserMeResponse,
)
from api.schemas.registration import (
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from api.security.jwt import create_access_token
from api.security.passwords import check_needs_rehash, hash_password, verify_password
from api.services.password_reset_service import PasswordResetService
from api.services.rate_limiter import RateLimiter, RateLimiterUnavailableError
from api.services.registration_service import RegistrationService
from api.services.session_service import SessionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


def _get_client_ip(request: Request) -> str:
    """Extract client IP safely from forwarded headers or direct connection."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Register new tenant and owner",
    description="Initiates staged self-service registration. Requires email verification before account creation.",
)
async def register(
    request_data: RegisterRequest,
    raw_request: Request,
    session: AsyncSession = Depends(get_session),
) -> RegisterResponse:
    """Initiate staged registration with anti-enumeration protection and rate limiting."""
    client_ip = _get_client_ip(raw_request)

    # Rate limiting with fail-closed policy on Redis unavailability
    try:
        allowed_ip, retry_after_ip = await RateLimiter.check_registration_ip(client_ip)
        if not allowed_ip:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many registration attempts. Please try again later.",
                headers={"Retry-After": str(retry_after_ip)},
            )

        allowed_email, retry_after_email = await RateLimiter.check_registration_email(
            request_data.email
        )
        if not allowed_email:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many registration attempts for this email. Please try again later.",
                headers={"Retry-After": str(retry_after_email)},
            )
    except RateLimiterUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiting service temporarily unavailable. Please try again later.",
        )

    return await RegistrationService.initiate_registration(request_data, session)


@router.post(
    "/verify-email",
    response_model=VerifyEmailResponse,
    summary="Verify email and activate account",
    description="Validates single-use token and creates permanent tenant and owner user entities.",
)
async def verify_email(
    request_data: VerifyEmailRequest,
    session: AsyncSession = Depends(get_session),
) -> VerifyEmailResponse:
    """Validate verification token and activate permanent tenant and user records."""
    return await RegistrationService.verify_email_token(request_data.token, session)


@router.post(
    "/resend-verification",
    response_model=ResendVerificationResponse,
    summary="Resend verification email",
    description="Issues and sends a fresh verification token if a valid pending registration exists.",
)
async def resend_verification(
    request_data: ResendVerificationRequest,
    session: AsyncSession = Depends(get_session),
) -> ResendVerificationResponse:
    """Resend verification email subject to cooldown constraints with fail-closed policy."""
    try:
        allowed, retry_after = await RateLimiter.check_resend_cooldown(request_data.email)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {retry_after} seconds before requesting another verification email.",
                headers={"Retry-After": str(retry_after)},
            )
    except RateLimiterUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiting service temporarily unavailable. Please try again later.",
        )

    return await RegistrationService.resend_verification(request_data.email, session)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="User login",
    description="Authenticate with email and password to receive a short-lived JWT access token and rotating refresh token.",
)
async def login(
    request: LoginRequest,
    raw_request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Authenticate credentials, create persistent session, and issue tokens."""
    client_ip = _get_client_ip(raw_request)
    email_clean = request.email.strip().lower()
    tenant_slug = (request.tenant_slug or "default").strip()

    # Rate limiting with fail-closed policy
    try:
        allowed_ip, retry_after_ip = await RateLimiter.check_login_ip(client_ip)
        if not allowed_ip:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts from this IP. Please try again later.",
                headers={"Retry-After": str(retry_after_ip)},
            )

        allowed_email, retry_after_email = await RateLimiter.check_login_email(email_clean)
        if not allowed_email:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts for this account. Please try again later.",
                headers={"Retry-After": str(retry_after_email)},
            )
    except RateLimiterUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiting service temporarily unavailable. Please try again later.",
        )

    # 1. Resolve tenant
    tenant_query = select(TenantDB).where(TenantDB.slug == tenant_slug)
    tenant_result = await session.execute(tenant_query)
    tenant = tenant_result.scalars().one_or_none()

    if not tenant or not tenant.is_active:
        # Generic response to prevent tenant/user enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. Resolve user within tenant
    user_query = select(UserDB).where(
        UserDB.email == email_clean,
        UserDB.tenant_id == tenant.id,
    )
    user_result = await session.execute(user_query)
    user = user_result.scalars().one_or_none()

    # 3. Verify user and password hash
    if not user or not user.is_active or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. Optional rehash on login if parameters upgraded
    if check_needs_rehash(user.password_hash):
        try:
            user.password_hash = hash_password(request.password)
            logger.info("Upgraded password hash parameters for user %s", user.id)
        except Exception as exc:
            logger.warning("Failed to rehash password on login: %s", exc)

    # 5. Update last login timestamp
    user.last_login_at = datetime.utcnow()
    await session.commit()

    # 6. Create persistent authentication session and issue rotating refresh token
    user_agent = raw_request.headers.get("user-agent")
    auth_session, raw_refresh_token = await SessionService.create_session(
        user=user,
        tenant=tenant,
        session=session,
        client_ip=client_ip,
        user_agent=user_agent,
    )

    # 7. Issue verified short-lived JWT access token bound to session_id
    token, expires_in = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        session_id=auth_session.id,
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        refresh_token=raw_refresh_token,
        session_id=auth_session.id,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and issue new access token",
    description="Atomically consumes the presented refresh token, checks for replay, rotates credentials, and issues a new access token.",
)
async def refresh(
    request_data: RefreshRequest,
    raw_request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Rotate refresh token and issue new access token with replay protection."""
    client_ip = _get_client_ip(raw_request)
    user_agent = raw_request.headers.get("user-agent")

    auth_session, jwt_token, new_raw_token, expires_in = (
        await SessionService.rotate_refresh_token(
            raw_refresh_token=request_data.refresh_token,
            session=session,
            client_ip=client_ip,
            user_agent=user_agent,
        )
    )

    return TokenResponse(
        access_token=jwt_token,
        token_type="bearer",
        expires_in=expires_in,
        refresh_token=new_raw_token,
        session_id=auth_session.id,
    )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Revoke session and logout",
    description="Revokes the active authentication session, invalidating both its refresh token and all associated access tokens immediately.",
)
async def logout(
    request_data: LogoutRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LogoutResponse:
    """Revoke user session(s) and fail closed on subsequent token presentations."""
    if request_data and request_data.all_sessions:
        count = await SessionService.revoke_all_user_sessions(
            user_id=current_user.user_id,
            session=session,
            reason="logout_all",
        )
        return LogoutResponse(
            message="All active sessions successfully revoked.",
            revoked_sessions=count,
        )

    if current_user.session_id:
        await SessionService.revoke_session(
            session_id=current_user.session_id,
            user_id=current_user.user_id,
            session=session,
            reason="logout",
        )

    return LogoutResponse(
        message="Successfully logged out.",
        revoked_sessions=1,
    )


@router.get(
    "/me",
    response_model=UserMeResponse,
    summary="Current authenticated user",
    description="Get safe profile and identity information for the current authenticated principal.",
)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserMeResponse:
    """Return safe summary of the authenticated user without exposing secrets or hashes."""
    user = await session.get(UserDB, current_user.user_id)
    display_name = user.display_name if user else None

    return UserMeResponse(
        user_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        session_id=current_user.session_id,
        email=current_user.email,
        display_name=display_name,
        role=current_user.role,
        tier=current_user.tier,
        auth_method=current_user.auth_method.value,
    )


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Request password recovery",
    description="Initiates password recovery. Dispatches a single-use token if the account exists, returning a generic response.",
)
async def forgot_password(
    request_data: ForgotPasswordRequest,
    raw_request: Request,
    session: AsyncSession = Depends(get_session),
) -> ForgotPasswordResponse:
    """Process password recovery request with per-IP and per-email rate limiting and fail-closed policy."""
    client_ip = _get_client_ip(raw_request)

    # Rate limiting with fail-closed policy
    try:
        allowed_ip, retry_after_ip = await RateLimiter.check_password_reset_ip(client_ip)
        if not allowed_ip:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many password recovery requests from this IP. Please try again later.",
                headers={"Retry-After": str(retry_after_ip)},
            )

        allowed_email, retry_after_email = await RateLimiter.check_password_reset_email(request_data.email)
        if not allowed_email:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many password recovery requests for this account. Please try again later.",
                headers={"Retry-After": str(retry_after_email)},
            )
    except RateLimiterUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiting service temporarily unavailable. Please try again later.",
        )

    msg = await PasswordResetService.request_password_reset(
        email=request_data.email,
        session=session,
        client_ip=client_ip,
        user_agent=raw_request.headers.get("user-agent"),
    )
    return ForgotPasswordResponse(message=msg)


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Reset password using recovery token",
    description="Validates recovery token and atomically updates password while revoking all existing sessions.",
)
async def reset_password(
    request_data: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
) -> ResetPasswordResponse:
    """Redeem recovery token to reset password, terminating all previous sessions."""
    msg = await PasswordResetService.reset_password(
        token=request_data.token,
        new_password=request_data.new_password,
        session=session,
    )
    return ResetPasswordResponse(message=msg)


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Change password for authenticated user",
    description="Verifies current password and updates to new password, revoking all existing user sessions.",
)
async def change_password(
    request_data: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChangePasswordResponse:
    """Update password for authenticated principal, terminating all sessions across all devices."""
    msg = await PasswordResetService.change_password(
        user_id=current_user.user_id,
        current_password=request_data.current_password,
        new_password=request_data.new_password,
        session=session,
    )
    return ChangePasswordResponse(message=msg)
