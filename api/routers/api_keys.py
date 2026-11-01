"""API Key Management Endpoints (STEP 02-C2)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.dependencies import (
    AuthMethod,
    CurrentUser,
    get_current_user,
    require_tenant_admin,
)
from api.schemas.api_keys import (
    APIKeyListResponse,
    APIKeyResponse,
    CreateAPIKeyRequest,
    CreateAPIKeyResponse,
    RevokeAPIKeyResponse,
    RotateAPIKeyRequest,
)
from api.security.api_keys import InvalidScopeError
from api.services.api_key_service import APIKeyService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-keys", tags=["API Keys"])


def _assert_user_session_only(user: CurrentUser) -> None:
    """Ensure that API keys can never manage other API keys or themselves."""
    if user.auth_method == AuthMethod.API_KEY or user.api_key_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys cannot administer API keys. An authenticated user session is required.",
        )


@router.post(
    "",
    response_model=CreateAPIKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create commercial API key",
    description="Generate a new scoped API key for the caller's tenant. Raw key secret is returned exactly once.",
)
async def create_api_key(
    request: CreateAPIKeyRequest,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_tenant_admin()),
):
    """Create a new API key."""
    _assert_user_session_only(user)

    # Commercial quota & feature enforcement (STEP 04-A2)
    from sqlalchemy import func, select
    from api.services.enforcement_service import is_tenant_enforced, CommercialEnforcementException
    from api.services.entitlement_service import resolve_effective_entitlements, QuotaReasonCode
    is_enf, rollout_mode = await is_tenant_enforced(session, user.tenant_id)
    effective, sub, plan = await resolve_effective_entitlements(session, user.tenant_id)

    api_gate = effective.get("api_access")
    if api_gate and api_gate.value_bool is False:
        msg = "API access is not included in current commercial subscription tier"
        if is_enf:
            raise CommercialEnforcementException(
                status_code=status.HTTP_403_FORBIDDEN,
                reason_code=QuotaReasonCode.FEATURE_NOT_INCLUDED.value,
                message=msg,
                metric="api_access",
            )
        elif rollout_mode == "shadow":
            from api.services.enforcement_service import _log_shadow_would_block
            await _log_shadow_would_block(session, user.tenant_id, QuotaReasonCode.FEATURE_NOT_INCLUDED.value, msg)

    key_limit_ent = effective.get("max_active_api_keys")
    if key_limit_ent and not key_limit_ent.is_unlimited:
        limit_val = key_limit_ent.value_int or 0
        from api.database.models import APIKeyDB
        stmt_cnt = select(func.count(APIKeyDB.id)).where(
            APIKeyDB.tenant_id == user.tenant_id,
            APIKeyDB.is_active == True,
            APIKeyDB.is_revoked == False,
        )
        active_key_count = (await session.execute(stmt_cnt)).scalar() or 0
        if active_key_count >= limit_val:
            msg = f"Maximum active API key limit reached ({active_key_count}/{limit_val})"
            if is_enf:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    reason_code=QuotaReasonCode.QUOTA_EXCEEDED.value,
                    message=msg,
                    metric="max_active_api_keys",
                    limit_value=float(limit_val),
                    current_value=float(active_key_count),
                )
            elif rollout_mode == "shadow":
                from api.services.enforcement_service import _log_shadow_would_block
                await _log_shadow_would_block(
                    session,
                    user.tenant_id,
                    QuotaReasonCode.QUOTA_EXCEEDED.value,
                    msg,
                    {"limit": limit_val, "current": active_key_count},
                )

    try:
        key, raw_secret = await APIKeyService.create_key(
            session=session,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            name=request.name,
            scopes=request.scopes,
            expires_in_days=request.expires_in_days,
            rate_limit=request.rate_limit or 60,
        )
    except InvalidScopeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return CreateAPIKeyResponse(
        id=key.id,
        name=key.name,
        prefix=key.key_prefix,
        key=raw_secret,
        scopes=key.permissions or [],
        rate_limit=key.rate_limit,
        expires_at=key.expires_at,
        created_at=key.created_at,
    )


@router.get(
    "",
    response_model=APIKeyListResponse,
    summary="List tenant API keys",
    description="List active and revoked API keys belonging to the caller's tenant. Secrets are never included.",
)
async def list_api_keys(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_tenant_admin()),
):
    """List API keys for caller tenant."""
    _assert_user_session_only(user)

    keys = await APIKeyService.list_keys(session, user.tenant_id)
    return APIKeyListResponse(
        keys=[APIKeyService.to_response_dto(k) for k in keys],
        total=len(keys),
    )


@router.get(
    "/{key_id}",
    response_model=APIKeyResponse,
    summary="Get API key details",
    description="Inspect public metadata of a specific API key.",
)
async def get_api_key(
    key_id: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_tenant_admin()),
):
    """Get single API key details with anti-enumeration protection."""
    _assert_user_session_only(user)

    key = await APIKeyService.get_key(session, user.tenant_id, key_id)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key {key_id} not found",
        )
    return APIKeyService.to_response_dto(key)


@router.delete(
    "/{key_id}",
    response_model=RevokeAPIKeyResponse,
    summary="Revoke API key",
    description="Permanently revoke an API key. Revocation takes effect immediately.",
)
async def revoke_api_key(
    key_id: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_tenant_admin()),
):
    """Revoke an API key."""
    _assert_user_session_only(user)

    key = await APIKeyService.revoke_key(
        session=session,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        key_id=key_id,
        reason="revoked_by_admin",
    )
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key {key_id} not found",
        )
    return RevokeAPIKeyResponse(
        message="API key revoked successfully",
        id=key.id,
        revoked_at=key.revoked_at,
    )


@router.post(
    "/{key_id}/rotate",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateAPIKeyResponse,
    summary="Rotate API key",
    description="Rotate an existing key, optionally retaining an overlap period. New raw secret returned exactly once.",
)
async def rotate_api_key(
    key_id: str,
    request: RotateAPIKeyRequest = RotateAPIKeyRequest(),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_tenant_admin()),
):
    """Rotate an API key."""
    _assert_user_session_only(user)

    # Commercial quota & feature enforcement (STEP 04-A2)
    from sqlalchemy import func, select
    from api.services.enforcement_service import is_tenant_enforced, CommercialEnforcementException
    from api.services.entitlement_service import resolve_effective_entitlements, QuotaReasonCode
    is_enf, rollout_mode = await is_tenant_enforced(session, user.tenant_id)
    effective, sub, plan = await resolve_effective_entitlements(session, user.tenant_id)

    api_gate = effective.get("api_access")
    if api_gate and api_gate.value_bool is False:
        msg = "API access is not included in current commercial subscription tier"
        if is_enf:
            raise CommercialEnforcementException(
                status_code=status.HTTP_403_FORBIDDEN,
                reason_code=QuotaReasonCode.FEATURE_NOT_INCLUDED.value,
                message=msg,
                metric="api_access",
            )
        elif rollout_mode == "shadow":
            from api.services.enforcement_service import _log_shadow_would_block
            await _log_shadow_would_block(session, user.tenant_id, QuotaReasonCode.FEATURE_NOT_INCLUDED.value, msg)

    key_limit_ent = effective.get("max_active_api_keys")
    if key_limit_ent and not key_limit_ent.is_unlimited:
        limit_val = key_limit_ent.value_int or 0
        from api.database.models import APIKeyDB
        stmt_cnt = select(func.count(APIKeyDB.id)).where(
            APIKeyDB.tenant_id == user.tenant_id,
            APIKeyDB.is_active == True,
            APIKeyDB.is_revoked == False,
        )
        active_key_count = (await session.execute(stmt_cnt)).scalar() or 0
        if active_key_count >= limit_val:
            msg = f"Maximum active API key limit reached ({active_key_count}/{limit_val})"
            if is_enf:
                raise CommercialEnforcementException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    reason_code=QuotaReasonCode.QUOTA_EXCEEDED.value,
                    message=msg,
                    metric="max_active_api_keys",
                    limit_value=float(limit_val),
                    current_value=float(active_key_count),
                )
            elif rollout_mode == "shadow":
                from api.services.enforcement_service import _log_shadow_would_block
                await _log_shadow_would_block(
                    session,
                    user.tenant_id,
                    QuotaReasonCode.QUOTA_EXCEEDED.value,
                    msg,
                    {"limit": limit_val, "current": active_key_count},
                )

    try:
        new_key, raw_secret = await APIKeyService.rotate_key(
            session=session,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            key_id=key_id,
            overlap_minutes=request.overlap_minutes or 0,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if not new_key or not raw_secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key {key_id} not found",
        )

    return CreateAPIKeyResponse(
        id=new_key.id,
        name=new_key.name,
        prefix=new_key.key_prefix,
        key=raw_secret,
        scopes=new_key.permissions or [],
        rate_limit=new_key.rate_limit,
        expires_at=new_key.expires_at,
        created_at=new_key.created_at,
    )
