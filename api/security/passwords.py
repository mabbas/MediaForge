"""Password hashing and verification using Argon2id (STEP 02-B1).

Adheres to RFC 9106 and OWASP recommendations:
- Algorithm: Argon2id
- Unique cryptographically secure salt generated automatically per hash
- Input length limits to prevent DoS via resource exhaustion
- Rehash checking for outdated hashes
- Zero sensitive data logging
"""

from __future__ import annotations

import logging
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

logger = logging.getLogger(__name__)

# Argon2id hasher with RFC 9106 recommended parameters:
# - Type: Argon2id (default in PasswordHasher)
# - Time cost (iterations): 3
# - Memory cost: 65536 KiB (64 MiB)
# - Parallelism: 4 threads
# - Salt length: 16 bytes
# - Hash length: 32 bytes
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

MAX_PASSWORD_LENGTH = 128
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id.

    Raises:
        ValueError: If password length is outside [MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH].
    """
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password exceeds maximum length of {MAX_PASSWORD_LENGTH} characters"
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    return _hasher.hash(password)


def verify_password(plain_password: str, password_hash: str | None) -> bool:
    """Verify a plaintext password against an Argon2id hash.

    Returns False safely without throwing on mismatch, invalid hash format, or excessive length.
    Never logs the plain password.
    """
    if not plain_password or not password_hash:
        return False
    if len(plain_password) > MAX_PASSWORD_LENGTH:
        return False

    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False
    except Exception as exc:
        logger.debug("Argon2 verification unexpected error: %s", type(exc).__name__)
        return False


def check_needs_rehash(password_hash: str | None) -> bool:
    """Check if the given hash was created with older/weaker Argon2 parameters."""
    if not password_hash:
        return True
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return True
