"""Password recovery, password reset, and authenticated password change service (STEP 02-B2C).

Implements:
- Generic anti-enumeration password recovery requests.
- Single-use, high-entropy cryptographic recovery tokens (stored as SHA-256 hashes).
- Configurable expiration (default: 30 minutes).
- Atomic single-use redemption with row-level locking.
- Atomic password update, token consumption, and whole-account session revocation.
- Authenticated password change with immediate session termination.
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
from api.database.models import PasswordResetTokenDB, UserDB
from api.security.passwords import hash_password, verify_password
from api.services.email_service import EmailService
from api.services.session_service import SessionService

logger = logging.getLogger(__name__)

RESERVED_SYSTEM_EMAILS = {
    "system@grabitdown.local",
    "system@mediaforge.local",
    "system@localhost",
    "root@localhost",
    "admin@localhost",
}

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000002"


def hash_reset_token(raw_token: str) -> str:
    """Compute SHA-256 hash of a raw recovery token."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


class PasswordResetService:
    """Business logic for password recovery, password reset, and password change."""

    @classmethod
    async def request_password_reset(
        cls,
        email: str,
        session: AsyncSession,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> str:
        """Process password recovery request.

        Always returns generic message regardless of whether the account exists,
        is inactive, or is a reserved system identity, preventing account enumeration.
        """
        email_clean = email.strip().lower()
        settings = get_api_settings()
        now = datetime.utcnow()

        generic_response = (
            "If the account exists and is eligible, a password reset link has been sent to your email."
        )

        # 1. Check for reserved system identities - never permit public recovery
        if email_clean in RESERVED_SYSTEM_EMAILS:
            logger.warning(
                "Password recovery request ignored for reserved system identity email."
            )
            return generic_response

        # 2. Query authoritative user
        stmt_user = select(UserDB).where(UserDB.email == email_clean)
        res_user = await session.execute(stmt_user)
        user = res_user.scalars().one_or_none()

        if not user or not user.is_active:
            # Nonexistent or inactive account: silently return generic message
            return generic_response

        if user.id == SYSTEM_USER_ID or user.role == "system":
            logger.warning(
                "Password recovery request ignored for reserved system user id: %s",
                user.id,
            )
            return generic_response

        # 3. Generate high-entropy token (32 URL-safe bytes >= 256 bits of entropy)
        raw_token = secrets.token_urlsafe(32)
        tok_hash = hash_reset_token(raw_token)
        expires_at = now + timedelta(minutes=settings.password_reset_token_expire_minutes)

        # 4. Invalidate any existing unconsumed tokens for this user
        stmt_invalidate = (
            update(PasswordResetTokenDB)
            .where(
                PasswordResetTokenDB.user_id == user.id,
                PasswordResetTokenDB.consumed_at == None,
            )
            .values(consumed_at=now)
        )
        await session.execute(stmt_invalidate)

        # 5. Persist new token record (hash only, never raw token)
        token_record = PasswordResetTokenDB(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=tok_hash,
            created_at=now,
            expires_at=expires_at,
            consumed_at=None,
            client_ip=client_ip,
            user_agent=user_agent[:500] if user_agent else None,
        )
        session.add(token_record)
        await session.commit()

        # 6. Dispatch transactional email via Mailpit (non-blocking)
        await EmailService.send_password_reset_email(
            to_email=user.email,
            raw_token=raw_token,
            expires_minutes=settings.password_reset_token_expire_minutes,
        )

        logger.info("Password recovery token issued and dispatched for user %s", user.id)
        return generic_response

    @classmethod
    async def reset_password(
        cls,
        token: str,
        new_password: str,
        session: AsyncSession,
    ) -> str:
        """Atomically validate token, update password hash, consume token, and revoke all sessions.

        Enforces single-use redemption with row-level locking.
        Rolls back password update and session revocation together if any step fails.
        """
        tok_hash = hash_reset_token(token)
        now = datetime.utcnow()

        # 1. Acquire exclusive row lock on reset token
        stmt_token = (
            select(PasswordResetTokenDB)
            .where(PasswordResetTokenDB.token_hash == tok_hash)
            .with_for_update()
        )
        res_token = await session.execute(stmt_token)
        token_record = res_token.scalars().one_or_none()

        if not token_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired password reset token",
            )

        if token_record.consumed_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password reset token has already been used",
            )

        if token_record.expires_at < now:
            token_record.consumed_at = now
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password reset token has expired",
            )

        # 2. Query user with row lock
        stmt_user = (
            select(UserDB)
            .where(UserDB.id == token_record.user_id)
            .with_for_update()
        )
        res_user = await session.execute(stmt_user)
        user = res_user.scalars().one_or_none()

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User account is inactive or not found",
            )

        # 3. Hash new password with Argon2id
        new_pwd_hash = hash_password(new_password)

        # 4. Atomic updates in single transaction
        user.password_hash = new_pwd_hash
        user.updated_at = now

        token_record.consumed_at = now

        # Invalidate any other outstanding reset tokens for this user
        stmt_invalidate_others = (
            update(PasswordResetTokenDB)
            .where(
                PasswordResetTokenDB.user_id == user.id,
                PasswordResetTokenDB.consumed_at == None,
            )
            .values(consumed_at=now)
        )
        await session.execute(stmt_invalidate_others)

        # Revoke all active sessions for this user (atomic commit=False)
        await SessionService.revoke_all_user_sessions(
            user_id=user.id,
            session=session,
            reason="password_reset",
            commit=False,
        )

        # 5. Commit all changes atomically
        await session.commit()

        logger.info("Password successfully reset and sessions revoked for user %s", user.id)
        return "Password has been successfully reset. Please log in with your new credentials."

    @classmethod
    async def change_password(
        cls,
        user_id: str,
        current_password: str,
        new_password: str,
        session: AsyncSession,
    ) -> str:
        """Authenticate current password, update to new Argon2id password hash, and revoke all sessions."""
        now = datetime.utcnow()

        # 1. Query user with row lock
        stmt_user = (
            select(UserDB)
            .where(UserDB.id == user_id)
            .with_for_update()
        )
        res_user = await session.execute(stmt_user)
        user = res_user.scalars().one_or_none()

        if not user or not user.is_active or not user.password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 2. Verify current password
        if not verify_password(current_password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 3. Hash new password with Argon2id
        new_pwd_hash = hash_password(new_password)

        # 4. Atomic updates
        user.password_hash = new_pwd_hash
        user.updated_at = now

        # Invalidate any outstanding reset tokens for this user
        stmt_invalidate_tokens = (
            update(PasswordResetTokenDB)
            .where(
                PasswordResetTokenDB.user_id == user.id,
                PasswordResetTokenDB.consumed_at == None,
            )
            .values(consumed_at=now)
        )
        await session.execute(stmt_invalidate_tokens)

        # Revoke all active sessions for this user (including current session)
        await SessionService.revoke_all_user_sessions(
            user_id=user.id,
            session=session,
            reason="password_change",
            commit=False,
        )

        # 5. Commit all changes atomically
        await session.commit()

        logger.info("Password successfully changed and sessions revoked for user %s", user.id)
        return "Password has been successfully changed. All existing sessions have been terminated. Please log in again."
