"""Safe administrative utility for user management and Super Admin bootstrap.

Allows:
1. Explicitly setting a password for an existing authorized user (set-password).
2. Bootstrapping or updating an authoritative Super Administrator (bootstrap-admin).

Security guarantees:
- Argon2id password hashing with length validation (8-128 chars).
- Credentials accepted via secure standard input (--password-stdin), secret file
  (--password-file), or interactive non-echoing terminal prompts (getpass).
- Never logs or exposes plaintext passwords in stdout, logs, or process command lines.
- Immutable audit trail recorded in commercial_audit_logs.
- Operates strictly under the application least-privilege DML role.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import getpass
import logging
import os
from pathlib import Path
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import get_api_settings
from api.database.models import CommercialAuditLogDB, TenantDB, UserDB
from api.security.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
)
from api.services.session_service import SessionService

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _read_credential(
    args: argparse.Namespace,
    prompt_label: str,
) -> str:
    """Read password securely from stdin, file, or interactive prompt.

    Avoids exposing credentials in process argument lists (/proc/$PID/cmdline).
    """
    if getattr(args, "password", None):
        raise ValueError(
            "Passing passwords via CLI argument (--password) is strictly prohibited "
            "to prevent exposing credentials in process lists (/proc/$PID/cmdline), "
            "system logs, and shell history. Use --password-stdin, --password-file, "
            "or interactive prompts."
        )

    if getattr(args, "password_stdin", False):
        line = sys.stdin.readline()
        if not line:
            raise ValueError("No password provided via stdin.")
        return line.rstrip("\r\n")

    if getattr(args, "password_file", None):
        secret_path = Path(args.password_file)
        if not secret_path.is_file():
            raise FileNotFoundError(f"Password file not found: {secret_path}")
        # On POSIX systems, verify file permissions (reject group/other read/write)
        if hasattr(os, "stat") and sys.platform != "win32":
            mode = secret_path.stat().st_mode
            if mode & 0o077:
                raise PermissionError(
                    f"Password file '{secret_path}' has insecure permissions ({oct(mode & 0o777)}). "
                    "File permissions must be 0600 or 0400 (not accessible by group or others)."
                )
        content = secret_path.read_text(encoding="utf-8")
        if not content.strip():
            raise ValueError("Password file is empty.")
        return content.rstrip("\r\n")

    # Interactive prompt fallback
    p1 = getpass.getpass(f"Enter {prompt_label}: ")
    p2 = getpass.getpass(f"Confirm {prompt_label}: ")
    if p1 != p2:
        raise ValueError("Passwords do not match.")
    return p1


async def set_user_password(
    email: str,
    password: str,
    tenant_slug: str = "default",
    database_url: str | None = None,
) -> bool:
    """Safely hash and set a password for an existing user account."""
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters."
        )

    settings = get_api_settings()
    db_url = database_url or settings.database_url

    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            # 1. Resolve tenant
            tenant_res = await session.execute(
                select(TenantDB).where(TenantDB.slug == tenant_slug.strip())
            )
            tenant = tenant_res.scalars().one_or_none()
            if not tenant:
                if tenant_slug.strip() == "default":
                    tenant = TenantDB(
                        id="00000000-0000-0000-0000-000000000001",
                        name="Default",
                        slug="default",
                        tier="platinum",
                        settings={"mode": "personal", "created_by": "bootstrap"},
                        is_active=True,
                    )
                    session.add(tenant)
                    await session.flush()
                    logger.info("Initialized default tenant '00000000-0000-0000-0000-000000000001'.")
                else:
                    logger.error("Tenant with slug '%s' not found.", tenant_slug)
                    return False

            # 2. Resolve existing user
            user_res = await session.execute(
                select(UserDB).where(
                    UserDB.email == email.strip().lower(),
                    UserDB.tenant_id == tenant.id,
                )
            )
            user = user_res.scalars().one_or_none()
            if not user:
                logger.error(
                    "User '%s' not found in tenant '%s'. Refusing to create new user.",
                    email,
                    tenant_slug,
                )
                return False

            # 3. Hash password with Argon2id and persist
            hashed = hash_password(password)
            user.password_hash = hashed
            await session.commit()
            logger.info("Successfully set password for user '%s' (tenant: '%s').", email, tenant_slug)
            return True
    finally:
        await engine.dispose()


async def bootstrap_super_admin(
    email: str,
    password: str,
    display_name: str = "Staging Super Administrator",
    tenant_slug: str = "default",
    role: str = "system_admin",
    rotate_existing: bool = False,
    reason: str | None = None,
    database_url: str | None = None,
) -> bool:
    """Idempotently bootstrap or rotate credentials for a Super Administrator account."""
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters."
        )

    settings = get_api_settings()
    db_url = database_url or settings.database_url

    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            # 1. Resolve tenant
            tenant_res = await session.execute(
                select(TenantDB).where(TenantDB.slug == tenant_slug.strip())
            )
            tenant = tenant_res.scalars().one_or_none()
            if not tenant:
                if tenant_slug.strip() == "default":
                    tenant = TenantDB(
                        id="00000000-0000-0000-0000-000000000001",
                        name="Default",
                        slug="default",
                        tier="platinum",
                        settings={"mode": "personal", "created_by": "bootstrap"},
                        is_active=True,
                    )
                    session.add(tenant)
                    await session.flush()
                    logger.info("Initialized default tenant '00000000-0000-0000-0000-000000000001'.")
                else:
                    logger.error("Tenant with slug '%s' not found.", tenant_slug)
                    return False

            # 2. Check for existing user
            normalized_email = email.strip().lower()
            user_res = await session.execute(
                select(UserDB).where(
                    UserDB.email == normalized_email,
                    UserDB.tenant_id == tenant.id,
                )
            )
            user = user_res.scalars().one_or_none()

            hashed = hash_password(password)
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            if user:
                if not rotate_existing:
                    raise ValueError(
                        f"Super administrator '{normalized_email}' already exists in tenant '{tenant_slug}'. "
                        "Repeated initial bootstrap fails closed by default to prevent unauthorized credential replacement. "
                        "To rotate credentials for an existing administrator, pass --rotate-existing and specify an explicit --reason."
                    )
                if not reason or not reason.strip():
                    raise ValueError(
                        "An explicit, non-empty audit reason (--reason) is required when rotating administrator credentials."
                    )

                # Revoke all active sessions and refresh tokens for the user
                revoked_sessions = await SessionService.revoke_all_user_sessions(
                    user_id=user.id,
                    session=session,
                    reason=f"credential_rotation: {reason.strip()}",
                    commit=False,
                )

                # Update existing user and rotate credentials
                user.password_hash = hashed
                user.role = role
                user.is_active = True
                if display_name:
                    user.display_name = display_name
                user.updated_at = now

                audit_log = CommercialAuditLogDB(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant.id,
                    action="SUPER_ADMIN_CREDENTIALS_ROTATED",
                    actor_user_id=user.id,
                    actor_role=role,
                    reason=reason.strip(),
                    details_json={
                        "email": normalized_email,
                        "display_name": user.display_name,
                        "tenant_slug": tenant_slug,
                        "role": role,
                        "sessions_revoked": revoked_sessions,
                        "method": "cli_rotation",
                    },
                    created_at=now,
                )
                session.add(audit_log)
                await session.commit()
                logger.info(
                    "Super administrator '%s' credentials rotated successfully. Revoked %d active sessions. Audit log recorded.",
                    normalized_email,
                    revoked_sessions,
                )
                return True
            else:
                if rotate_existing:
                    raise ValueError(
                        f"Cannot rotate credentials: administrator '{normalized_email}' does not exist in tenant '{tenant_slug}'. "
                        "Run bootstrap-admin without --rotate-existing to perform initial administrative bootstrap."
                    )

                # Create fresh administrator
                admin_id = str(uuid.uuid4())
                new_admin = UserDB(
                    id=admin_id,
                    tenant_id=tenant.id,
                    email=normalized_email,
                    display_name=display_name,
                    password_hash=hashed,
                    auth_provider="local",
                    role=role,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(new_admin)

                audit_log = CommercialAuditLogDB(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant.id,
                    action="SUPER_ADMIN_BOOTSTRAPPED",
                    actor_user_id=admin_id,
                    actor_role=role,
                    reason="Operator bootstrap of initial administrative account via CLI",
                    details_json={
                        "email": normalized_email,
                        "display_name": display_name,
                        "tenant_slug": tenant_slug,
                        "role": role,
                        "method": "cli_bootstrap",
                    },
                    created_at=now,
                )
                session.add(audit_log)
                await session.commit()
                logger.info(
                    "Super administrator '%s' bootstrapped successfully (role: '%s', tenant: '%s'). Audit log recorded.",
                    normalized_email,
                    role,
                    tenant_slug,
                )
                return True
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="MediaForge User Management & Super Admin Bootstrap Utility"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. set-password subcommand
    pwd_parser = subparsers.add_parser(
        "set-password",
        help="Set password for an existing authorized account using Argon2id",
    )
    pwd_parser.add_argument(
        "--email",
        required=True,
        help="Email address of the existing user",
    )
    pwd_parser.add_argument(
        "--tenant",
        default="default",
        help="Tenant slug (default: 'default')",
    )
    pwd_parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read password from standard input (preferred for scripts and pipes)",
    )
    pwd_parser.add_argument(
        "--password-file",
        default=None,
        help="Read password from a secure file path",
    )
    pwd_parser.add_argument(
        "--password",
        default=None,
        help="Plaintext password via CLI argument (INSECURE: logs in /proc/$PID/cmdline)",
    )

    # 2. bootstrap-admin subcommand
    boot_parser = subparsers.add_parser(
        "bootstrap-admin",
        help="Bootstrap a Super Administrator account with Argon2id and audit logging",
    )
    boot_parser.add_argument(
        "--email",
        required=True,
        help="Email address of the administrator account",
    )
    boot_parser.add_argument(
        "--display-name",
        default="Staging Super Administrator",
        help="Display name for the administrator",
    )
    boot_parser.add_argument(
        "--tenant",
        default="default",
        help="Tenant slug (default: 'default')",
    )
    boot_parser.add_argument(
        "--role",
        default="system_admin",
        choices=["system_admin", "system", "admin", "owner"],
        help="Administrative role (default: 'system_admin')",
    )
    boot_parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read password from standard input (preferred for scripts and pipes)",
    )
    boot_parser.add_argument(
        "--password-file",
        default=None,
        help="Read password from a secure file path",
    )
    boot_parser.add_argument(
        "--password",
        default=None,
        help="Plaintext password via CLI argument (STRICTLY PROHIBITED; will be rejected)",
    )
    boot_parser.add_argument(
        "--rotate-existing",
        action="store_true",
        help="Explicitly permit rotating credentials for an existing administrator",
    )
    boot_parser.add_argument(
        "--reason",
        default=None,
        help="Mandatory operator audit reason when rotating credentials (--rotate-existing)",
    )

    args = parser.parse_args()

    try:
        if args.command == "set-password":
            password = _read_credential(args, f"new password for {args.email}")
            ok = asyncio.run(
                set_user_password(
                    email=args.email,
                    password=password,
                    tenant_slug=args.tenant,
                )
            )
            if not ok:
                sys.exit(1)

        elif args.command == "bootstrap-admin":
            password = _read_credential(args, f"administrator password for {args.email}")
            ok = asyncio.run(
                bootstrap_super_admin(
                    email=args.email,
                    password=password,
                    display_name=args.display_name,
                    tenant_slug=args.tenant,
                    role=args.role,
                    rotate_existing=args.rotate_existing,
                    reason=args.reason,
                )
            )
            if not ok:
                sys.exit(1)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
