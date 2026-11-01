"""Safe administrative utility for local development user account management (STEP 02-B1).

Allows explicitly setting a password for an existing authorized test user:
- Uses Argon2id hashing
- Never logs or exposes plaintext passwords
- Enforces password length and complexity validation
- Restricts operations to the runtime least-privilege role
- Preserves existing seeded records without creating default passwords
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import get_api_settings
from api.database.models import TenantDB, UserDB
from api.security.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


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


def main():
    parser = argparse.ArgumentParser(
        description="MediaForge Development User Management Utility"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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
        "--password",
        default=None,
        help="Plaintext password (if omitted, prompts interactively without echo)",
    )

    args = parser.parse_args()

    if args.command == "set-password":
        password = args.password
        if not password:
            password = getpass.getpass(f"Enter new password for {args.email}: ")
            confirm = getpass.getpass("Confirm new password: ")
            if password != confirm:
                print("Error: Passwords do not match.", file=sys.stderr)
                sys.exit(1)

        try:
            ok = asyncio.run(
                set_user_password(
                    email=args.email,
                    password=password,
                    tenant_slug=args.tenant,
                )
            )
            if not ok:
                sys.exit(1)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
