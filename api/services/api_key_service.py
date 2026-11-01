"""API Key business logic and persistence service (STEP 02-C2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.models import APIKeyDB, TenantDB
from api.schemas.api_keys import APIKeyResponse
from api.security.api_keys import (
    generate_api_key,
    hash_api_key,
    mask_api_key,
    validate_scopes,
    verify_api_key,
)

logger = logging.getLogger(__name__)


class APIKeyService:
    """Service handling API key lifecycle operations."""

    @staticmethod
    def to_response_dto(key: APIKeyDB) -> APIKeyResponse:
        """Map APIKeyDB model to safe public APIKeyResponse schema."""
        return APIKeyResponse(
            id=key.id,
            name=key.name,
            prefix=key.key_prefix,
            masked_key=mask_api_key(key.key_prefix),
            scopes=key.permissions or [],
            rate_limit=key.rate_limit,
            is_active=key.is_active,
            is_revoked=key.is_revoked,
            revoked_at=key.revoked_at,
            revocation_reason=key.revocation_reason,
            rotated_to_id=key.rotated_to_id,
            total_requests=key.total_requests or 0,
            expires_at=key.expires_at,
            last_used_at=key.last_used_at,
            created_at=key.created_at,
        )

    @classmethod
    async def create_key(
        cls,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        name: str,
        scopes: Sequence[str],
        expires_in_days: Optional[int] = None,
        rate_limit: int = 60,
    ) -> tuple[APIKeyDB, str]:
        """Create a new commercial API key for a tenant."""
        validated_scopes = validate_scopes(scopes)
        raw_key, prefix, key_hash = generate_api_key()

        expires_at = None
        if expires_in_days is not None:
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expires_in_days)

        db_key = APIKeyDB(
            name=name.strip(),
            key_prefix=prefix,
            key_hash=key_hash,
            tenant_id=tenant_id,
            user_id=user_id,
            permissions=validated_scopes,
            rate_limit=rate_limit,
            is_active=True,
            is_revoked=False,
            expires_at=expires_at,
        )
        session.add(db_key)
        await session.commit()
        await session.refresh(db_key)
        return db_key, raw_key

    @classmethod
    async def list_keys(
        cls,
        session: AsyncSession,
        tenant_id: str,
    ) -> list[APIKeyDB]:
        """List all API keys belonging to a tenant."""
        stmt = (
            select(APIKeyDB)
            .where(APIKeyDB.tenant_id == tenant_id)
            .order_by(APIKeyDB.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def get_key(
        cls,
        session: AsyncSession,
        tenant_id: str,
        key_id: str,
    ) -> Optional[APIKeyDB]:
        """Retrieve a specific key ensuring tenant ownership."""
        stmt = select(APIKeyDB).where(
            APIKeyDB.id == key_id,
            APIKeyDB.tenant_id == tenant_id,
        )
        result = await session.execute(stmt)
        return result.scalars().one_or_none()

    @classmethod
    async def revoke_key(
        cls,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        key_id: str,
        reason: str = "revoked_by_admin",
    ) -> Optional[APIKeyDB]:
        """Revoke an API key with row locking for concurrency safety."""
        stmt = (
            select(APIKeyDB)
            .where(
                APIKeyDB.id == key_id,
                APIKeyDB.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        result = await session.execute(stmt)
        key = result.scalars().one_or_none()
        if not key:
            return None

        key.is_revoked = True
        key.is_active = False
        key.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        key.revocation_reason = reason
        key.revoked_by_user_id = user_id
        await session.commit()
        await session.refresh(key)
        return key

    @classmethod
    async def rotate_key(
        cls,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        key_id: str,
        overlap_minutes: int = 0,
    ) -> tuple[Optional[APIKeyDB], Optional[str]]:
        """Rotate an existing key, optionally applying an overlap window."""
        stmt = (
            select(APIKeyDB)
            .where(
                APIKeyDB.id == key_id,
                APIKeyDB.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        result = await session.execute(stmt)
        old_key = result.scalars().one_or_none()
        if not old_key:
            return None, None

        if old_key.is_revoked:
            raise ValueError("Cannot rotate an already revoked API key.")

        raw_new_key, new_prefix, new_hash = generate_api_key()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        new_key = APIKeyDB(
            name=f"{old_key.name} (Rotated)",
            key_prefix=new_prefix,
            key_hash=new_hash,
            tenant_id=tenant_id,
            user_id=user_id,
            permissions=list(old_key.permissions or []),
            rate_limit=old_key.rate_limit,
            is_active=True,
            is_revoked=False,
            rotated_from_id=old_key.id,
            expires_at=old_key.expires_at,
        )
        session.add(new_key)
        await session.flush()

        old_key.rotated_to_id = new_key.id
        if overlap_minutes <= 0:
            old_key.is_revoked = True
            old_key.is_active = False
            old_key.revoked_at = now
            old_key.revocation_reason = "rotated_immediate"
            old_key.revoked_by_user_id = user_id
        else:
            overlap_expiry = now + timedelta(minutes=overlap_minutes)
            if old_key.expires_at is None or overlap_expiry < old_key.expires_at:
                old_key.expires_at = overlap_expiry
            old_key.revocation_reason = f"rotated_with_overlap_{overlap_minutes}m"

        await session.commit()
        await session.refresh(new_key)
        return new_key, raw_new_key

    @classmethod
    async def resolve_key_by_secret(
        cls,
        session: AsyncSession,
        raw_key: str,
    ) -> Optional[tuple[APIKeyDB, TenantDB]]:
        """Validate presented raw API key and resolve its active DB record and tenant."""
        if not raw_key or not raw_key.startswith("mf_live_"):
            return None

        presented_hash = hash_api_key(raw_key)

        stmt = (
            select(APIKeyDB, TenantDB)
            .join(TenantDB, APIKeyDB.tenant_id == TenantDB.id)
            .where(APIKeyDB.key_hash == presented_hash)
        )
        result = await session.execute(stmt)
        row = result.first()
        if not row:
            return None

        key, tenant = row
        # Constant-time comparison
        if not verify_api_key(raw_key, key.key_hash):
            return None

        return key, tenant
