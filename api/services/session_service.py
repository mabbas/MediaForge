"""Authentication session and rotating refresh token service (STEP 02-B2B).

Enforces:
- Cryptographically secure opaque refresh tokens (48 urlsafe bytes = 384 bits of entropy)
- SHA-256 token hashing prior to persistence (no plaintext tokens stored)
- Atomic single-use rotation with PostgreSQL row locking (SELECT ... FOR UPDATE)
- Immediate replay detection: consumed token reuse revokes the entire token family
- Authoritative session revocation and access-token invalidation
- Configurable session expiration (default 30 days)
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import logging
import secrets
from typing import Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_api_settings
from api.database.models import AuthSessionDB, ConsumedRefreshTokenDB, TenantDB, UserDB
from api.security.jwt import create_access_token

logger = logging.getLogger(__name__)


def hash_refresh_token(raw_token: str) -> str:
    """Compute SHA-256 hash of a refresh token."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


class SessionService:
    """Orchestrates persistent session lifecycle, rotation, replay detection, and revocation."""

    @classmethod
    async def create_session(
        cls,
        user: UserDB,
        tenant: TenantDB,
        session: AsyncSession,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[AuthSessionDB, str]:
        """Create a new persistent session and issue a rotating opaque refresh token."""
        settings = get_api_settings()
        now = datetime.utcnow()
        expires_at = now + timedelta(days=settings.refresh_token_expire_days)

        token_family = uuid.uuid4().hex
        raw_refresh_token = secrets.token_urlsafe(48)
        token_hash = hash_refresh_token(raw_refresh_token)
        session_id = str(uuid.uuid4())

        auth_session = AuthSessionDB(
            id=session_id,
            user_id=user.id,
            tenant_id=tenant.id,
            token_family=token_family,
            current_token_hash=token_hash,
            rotation_counter=0,
            is_revoked=False,
            revoked_at=None,
            revocation_reason=None,
            client_ip=client_ip,
            user_agent=user_agent[:500] if user_agent else None,
            last_refreshed_at=None,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        session.add(auth_session)
        await session.commit()

        logger.info(
            "Created auth session %s for user %s (tenant: %s)",
            session_id,
            user.id,
            tenant.slug,
        )
        return auth_session, raw_refresh_token

    @classmethod
    async def rotate_refresh_token(
        cls,
        raw_refresh_token: str,
        session: AsyncSession,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[AuthSessionDB, str, str, int]:
        """Atomically consume an old refresh token, detect replay, rotate credentials, and issue access token.

        Returns:
            (auth_session, access_token, new_raw_refresh_token, expires_in)
        """
        presented_hash = hash_refresh_token(raw_refresh_token)
        now = datetime.utcnow()

        # 1. Replay detection check against consumed tokens
        stmt_consumed = select(ConsumedRefreshTokenDB).where(
            ConsumedRefreshTokenDB.token_hash == presented_hash
        )
        res_consumed = await session.execute(stmt_consumed)
        consumed = res_consumed.scalar_one_or_none()

        if consumed:
            # Replay detected! Revoke the entire session family immediately
            affected_session = await session.get(AuthSessionDB, consumed.session_id)
            if affected_session:
                family = affected_session.token_family
                stmt_revoke_family = (
                    update(AuthSessionDB)
                    .where(AuthSessionDB.token_family == family)
                    .values(
                        is_revoked=True,
                        revoked_at=now,
                        revocation_reason="token_reuse_detected",
                        updated_at=now,
                    )
                )
                await session.execute(stmt_revoke_family)
                await session.commit()
                logger.warning(
                    "Replay attack detected on consumed refresh token. Session family %s revoked.",
                    family,
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token. Token reuse detected and session revoked.",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        # 2. Row-level exclusive lock on active session matching token hash
        stmt_session = (
            select(AuthSessionDB)
            .where(AuthSessionDB.current_token_hash == presented_hash)
            .with_for_update()
        )
        res_session = await session.execute(stmt_session)
        auth_session = res_session.scalar_one_or_none()

        if not auth_session:
            # Re-check consumed tokens in case token was rotated concurrently while waiting on row lock
            stmt_consumed_retry = select(ConsumedRefreshTokenDB).where(
                ConsumedRefreshTokenDB.token_hash == presented_hash
            )
            res_consumed_retry = await session.execute(stmt_consumed_retry)
            consumed_retry = res_consumed_retry.scalar_one_or_none()
            if consumed_retry:
                affected_session = await session.get(AuthSessionDB, consumed_retry.session_id)
                if affected_session:
                    family = affected_session.token_family
                    stmt_revoke_family = (
                        update(AuthSessionDB)
                        .where(AuthSessionDB.token_family == family)
                        .values(
                            is_revoked=True,
                            revoked_at=now,
                            revocation_reason="token_reuse_detected",
                            updated_at=now,
                        )
                    )
                    await session.execute(stmt_revoke_family)
                    await session.commit()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token. Token reuse detected and session revoked.",
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        # 3. Check revocation
        if auth_session.is_revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        # 4. Check expiration
        if auth_session.expires_at < now:
            auth_session.is_revoked = True
            auth_session.revoked_at = now
            auth_session.revocation_reason = "expired"
            auth_session.updated_at = now
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has expired",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        # 5. Check user and tenant status
        user = await session.get(UserDB, auth_session.user_id)
        if not user or not user.is_active:
            auth_session.is_revoked = True
            auth_session.revoked_at = now
            auth_session.revocation_reason = "user_inactive"
            auth_session.updated_at = now
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive or not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        tenant = await session.get(TenantDB, auth_session.tenant_id)
        if not tenant or not tenant.is_active:
            auth_session.is_revoked = True
            auth_session.revoked_at = now
            auth_session.revocation_reason = "tenant_inactive"
            auth_session.updated_at = now
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Tenant account is inactive or not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 6. Record old token into consumed history
        consumed_record = ConsumedRefreshTokenDB(
            id=str(uuid.uuid4()),
            session_id=auth_session.id,
            token_hash=presented_hash,
            consumed_at=now,
            expires_at=auth_session.expires_at,
        )
        session.add(consumed_record)

        # 7. Generate replacement refresh token
        new_raw_token = secrets.token_urlsafe(48)
        new_token_hash = hash_refresh_token(new_raw_token)

        auth_session.current_token_hash = new_token_hash
        auth_session.rotation_counter += 1
        auth_session.last_refreshed_at = now
        auth_session.updated_at = now
        if client_ip:
            auth_session.client_ip = client_ip
        if user_agent:
            auth_session.user_agent = user_agent[:500]

        # 8. Atomic commit
        await session.commit()

        # 9. Issue new JWT access token bound to session_id
        jwt_token, expires_in = create_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            session_id=auth_session.id,
        )

        logger.info(
            "Rotated refresh token for session %s (counter: %d)",
            auth_session.id,
            auth_session.rotation_counter,
        )
        return auth_session, jwt_token, new_raw_token, expires_in

    @classmethod
    async def revoke_session(
        cls,
        session_id: str,
        user_id: str,
        session: AsyncSession,
        reason: str = "logout",
        commit: bool = True,
    ) -> bool:
        """Revoke a specific session belonging to the user."""
        auth_session = await session.get(AuthSessionDB, session_id)
        if not auth_session:
            return False

        if auth_session.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot revoke foreign user session",
            )

        if not auth_session.is_revoked:
            now = datetime.utcnow()
            auth_session.is_revoked = True
            auth_session.revoked_at = now
            auth_session.revocation_reason = reason
            auth_session.updated_at = now
            if commit:
                await session.commit()
            logger.info("Revoked session %s for user %s (reason: %s)", session_id, user_id, reason)
        return True

    @classmethod
    async def revoke_all_user_sessions(
        cls,
        user_id: str,
        session: AsyncSession,
        reason: str = "logout_all",
        commit: bool = True,
    ) -> int:
        """Revoke all active sessions for a user across all devices."""
        now = datetime.utcnow()
        stmt = (
            update(AuthSessionDB)
            .where(AuthSessionDB.user_id == user_id, AuthSessionDB.is_revoked == False)
            .values(
                is_revoked=True,
                revoked_at=now,
                revocation_reason=reason,
                updated_at=now,
            )
        )
        res = await session.execute(stmt)
        if commit:
            await session.commit()
        logger.info("Revoked %d sessions for user %s (reason: %s)", res.rowcount, user_id, reason)
        return res.rowcount
