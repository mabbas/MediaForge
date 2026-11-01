"""Registration service implementing staged tenant/user creation and verification (STEP 02-B2A).

Enforces:
- Staged pending registration (no permanent DB entities created until verification)
- Single-use SHA-256 token validation
- Argon2id password hashing prior to staging
- 24-hour expiration lifecycle
- Reserved slug protection & auto-slug generation
- Least-privilege tenant tier ('basic', never 'platinum')
- Owner role assignment for new tenants
- Anti-enumeration generic responses
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import logging
import re
import secrets
from typing import Optional
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_api_settings
from api.database.models import PendingRegistrationDB, TenantDB, UserDB
from api.schemas.registration import (
    RegisterRequest,
    RegisterResponse,
    ResendVerificationResponse,
    VerifyEmailResponse,
)
from api.security.passwords import hash_password
from api.services.email_service import EmailService

logger = logging.getLogger(__name__)

RESERVED_SLUGS = {
    "admin",
    "administrator",
    "api",
    "app",
    "auth",
    "billing",
    "dashboard",
    "default",
    "docs",
    "download",
    "downloads",
    "graphql",
    "health",
    "help",
    "internal",
    "jobs",
    "login",
    "logout",
    "media",
    "mediaforge",
    "metrics",
    "oauth",
    "public",
    "register",
    "root",
    "settings",
    "static",
    "status",
    "support",
    "system",
    "transcripts",
    "users",
    "webhook",
    "webhooks",
}


def hash_token(raw_token: str) -> str:
    """Compute SHA-256 hash of a verification token."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def generate_slug_candidate(name: str) -> str:
    """Generate a clean kebab-case slug candidate from an organization name."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    if not cleaned:
        cleaned = "org"
    if len(cleaned) < 3:
        cleaned = f"{cleaned}-{secrets.token_hex(2)}"
    return cleaned[:45]


class RegistrationService:
    """Orchestrates staged registration, token lifecycle, and account activation."""

    @classmethod
    async def resolve_unique_slug(
        cls,
        requested_slug: Optional[str],
        tenant_name: str,
        session: AsyncSession,
        exclude_email: Optional[str] = None,
    ) -> str:
        """Resolve a unique, unreserved tenant slug."""
        base_slug = requested_slug or generate_slug_candidate(tenant_name)

        if base_slug in RESERVED_SLUGS:
            if requested_slug:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"The tenant slug '{requested_slug}' is reserved and cannot be used",
                )
            base_slug = f"{base_slug}-{secrets.token_hex(2)}"

        candidate = base_slug
        suffix_counter = 1

        while True:
            # Check permanent tenants
            stmt_tenant = select(TenantDB.id).where(TenantDB.slug == candidate)
            res_tenant = await session.execute(stmt_tenant)
            if res_tenant.scalar_one_or_none() is None:
                # Check active pending registrations
                stmt_pending = select(PendingRegistrationDB).where(
                    PendingRegistrationDB.tenant_slug == candidate,
                    PendingRegistrationDB.expires_at > datetime.utcnow(),
                )
                if exclude_email:
                    stmt_pending = stmt_pending.where(PendingRegistrationDB.email != exclude_email)
                res_pending = await session.execute(stmt_pending)
                if res_pending.scalar_one_or_none() is None:
                    return candidate

            # Slug is taken: if user explicitly requested a conflicting slug, reject it
            if requested_slug and suffix_counter == 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"The tenant slug '{requested_slug}' is already in use",
                )

            candidate = f"{base_slug[:40]}-{secrets.token_hex(2)}"
            suffix_counter += 1
            if suffix_counter > 10:
                candidate = f"org-{uuid.uuid4().hex[:8]}"
                return candidate

    @classmethod
    async def initiate_registration(
        cls,
        request: RegisterRequest,
        session: AsyncSession,
    ) -> RegisterResponse:
        """Initiate staged registration workflow.

        - If email is already in permanent UserDB, return generic response without leaking existence.
        - Staged in PendingRegistrationDB with Argon2id password hash and SHA-256 token hash.
        - Sends verification email with single-use raw token.
        """
        email_clean = request.email.strip().lower()
        settings = get_api_settings()

        # 1. Anti-enumeration check against existing permanent users
        stmt_user = select(UserDB.id).where(UserDB.email == email_clean)
        res_user = await session.execute(stmt_user)
        if res_user.scalar_one_or_none() is not None:
            logger.info("Registration attempt for existing user email: [masked]")
            # Return generic success response without creating pending or sending token
            return RegisterResponse(
                message="Registration initiated. If this email is eligible, a verification link has been sent.",
                email=email_clean,
            )

        # 2. Resolve or validate tenant slug
        slug = await cls.resolve_unique_slug(
            requested_slug=request.tenant_slug,
            tenant_name=request.tenant_name,
            session=session,
            exclude_email=email_clean,
        )

        # 3. Hash password using Argon2id
        pwd_hash = hash_password(request.password)

        # 4. Generate cryptographically secure token & its SHA-256 hash
        raw_token = secrets.token_urlsafe(32)
        tok_hash = hash_token(raw_token)
        now = datetime.utcnow()
        expires_at = now + timedelta(hours=settings.registration_token_expire_hours)

        # 5. Check if pending registration already exists for this email
        stmt_pending = select(PendingRegistrationDB).where(
            PendingRegistrationDB.email == email_clean
        )
        res_pending = await session.execute(stmt_pending)
        existing_pending = res_pending.scalar_one_or_none()

        if existing_pending:
            existing_pending.password_hash = pwd_hash
            existing_pending.tenant_name = request.tenant_name
            existing_pending.tenant_slug = slug
            existing_pending.token_hash = tok_hash
            existing_pending.expires_at = expires_at
            existing_pending.last_sent_at = now
            existing_pending.attempts = 0
            existing_pending.updated_at = now
        else:
            new_pending = PendingRegistrationDB(
                id=str(uuid.uuid4()),
                email=email_clean,
                password_hash=pwd_hash,
                tenant_name=request.tenant_name,
                tenant_slug=slug,
                token_hash=tok_hash,
                expires_at=expires_at,
                attempts=0,
                last_sent_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(new_pending)

        await session.commit()

        # 6. Dispatch verification email via Mailpit (non-blocking)
        await EmailService.send_verification_email(
            to_email=email_clean,
            tenant_name=request.tenant_name,
            raw_token=raw_token,
            expires_hours=settings.registration_token_expire_hours,
        )

        return RegisterResponse(
            message="Registration initiated. If this email is eligible, a verification link has been sent.",
            email=email_clean,
        )

    @classmethod
    async def verify_email_token(
        cls,
        raw_token: str,
        session: AsyncSession,
    ) -> VerifyEmailResponse:
        """Verify token, atomically create permanent tenant and owner user, and remove pending record."""
        tok_hash = hash_token(raw_token)
        now = datetime.utcnow()

        stmt = (
            select(PendingRegistrationDB)
            .where(PendingRegistrationDB.token_hash == tok_hash)
            .with_for_update()
        )
        res = await session.execute(stmt)
        pending = res.scalar_one_or_none()

        if not pending:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification token",
            )

        # Check expiration
        if pending.expires_at < now:
            await session.delete(pending)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification token has expired. Please register again or request a new link.",
            )

        # Check if email was registered concurrently
        stmt_existing_user = select(UserDB.id).where(UserDB.email == pending.email)
        res_existing_user = await session.execute(stmt_existing_user)
        if res_existing_user.scalar_one_or_none() is not None:
            await session.delete(pending)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email address already exists.",
            )

        # Re-verify slug uniqueness; if taken in the interim, generate unique suffix
        final_slug = pending.tenant_slug
        stmt_check_slug = select(TenantDB.id).where(TenantDB.slug == final_slug)
        res_check_slug = await session.execute(stmt_check_slug)
        if res_check_slug.scalar_one_or_none() is not None:
            final_slug = f"{final_slug[:40]}-{secrets.token_hex(2)}"

        tenant_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        # 1. Create permanent Tenant
        tenant = TenantDB(
            id=tenant_id,
            name=pending.tenant_name,
            slug=final_slug,
            tier="basic",  # STRICT: Self-service registration must never assign platinum tier!
            settings={},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)

        # 2. Create permanent Owner User
        user = UserDB(
            id=user_id,
            tenant_id=tenant_id,
            email=pending.email,
            password_hash=pending.password_hash,
            role="owner",
            is_active=True,
            display_name=pending.tenant_name,
            created_at=now,
            updated_at=now,
        )
        session.add(user)

        # 3. Single-use: delete pending registration
        await session.delete(pending)

        # 4. Assign initial commercial subscription based on persistent default trial configuration (STEP 04-A1-Fix-A)
        try:
            from api.services.entitlement_service import get_system_default_trial_days
            from api.database.models import TenantSubscriptionDB

            trial_days = await get_system_default_trial_days(session)
            if trial_days > 0:
                sub = TenantSubscriptionDB(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    plan_id="plan-trial-v1",
                    status="trial",
                    trial_started_at=now,
                    trial_expires_at=now + timedelta(days=trial_days),
                    current_period_start=now,
                    created_at=now,
                    updated_at=now,
                )
            else:
                # 0-day trial: immediate expired status with zero positive trial access window
                sub = TenantSubscriptionDB(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    plan_id="plan-trial-v1",
                    status="expired",
                    trial_started_at=now,
                    trial_expires_at=now,
                    current_period_start=now,
                    created_at=now,
                    updated_at=now,
                )
            session.add(sub)
        except Exception as sub_exc:
            logger.warning("Could not assign initial subscription for new tenant %s: %s", tenant_id, sub_exc)

        # 5. Atomic commit
        await session.commit()

        logger.info(
            "Successfully activated tenant '%s' (%s) and owner user '%s' (%s)",
            tenant.name,
            tenant.id,
            user.email,
            user.id,
        )

        return VerifyEmailResponse(
            message="Email verified successfully. You may now log in.",
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            user_id=user.id,
            email=user.email,
        )

    @classmethod
    async def resend_verification(
        cls,
        email: str,
        session: AsyncSession,
    ) -> ResendVerificationResponse:
        """Resend a fresh verification link to a pending registration."""
        email_clean = email.strip().lower()
        settings = get_api_settings()
        now = datetime.utcnow()

        stmt = select(PendingRegistrationDB).where(
            PendingRegistrationDB.email == email_clean
        )
        res = await session.execute(stmt)
        pending = res.scalar_one_or_none()

        if not pending:
            # Generic response to prevent enumeration
            return ResendVerificationResponse(
                message="If a pending registration exists for this email, a new verification link has been sent."
            )

        # Generate fresh token & reset expiration
        raw_token = secrets.token_urlsafe(32)
        pending.token_hash = hash_token(raw_token)
        pending.expires_at = now + timedelta(hours=settings.registration_token_expire_hours)
        pending.last_sent_at = now
        pending.attempts += 1
        pending.updated_at = now

        await session.commit()

        await EmailService.send_verification_email(
            to_email=pending.email,
            tenant_name=pending.tenant_name,
            raw_token=raw_token,
            expires_hours=settings.registration_token_expire_hours,
        )

        return ResendVerificationResponse(
            message="If a pending registration exists for this email, a new verification link has been sent."
        )
