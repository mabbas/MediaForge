"""API key generation, cryptographic hashing, constant-time verification, and scope validation (STEP 02-C2)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Sequence

KEY_PREFIX = "mf_live_"
VALID_API_SCOPES = {
    "downloads:read": "View download status, progress, history, and retrieve downloaded files.",
    "downloads:write": "Queue new downloads, batch downloads, playlists, and manage job states.",
    "clips:read": "Validate clip boundaries and retrieve extracted clip artifacts.",
    "clips:write": "Trigger video clip extraction and multi-clip merging operations.",
    "transcripts:read": "Read stored transcripts and transcript metadata.",
    "transcripts:write": "Request on-demand transcript extraction and generation.",
    "usage:read": "Query tenant-level and user-level usage metrics.",
    "providers:read": "Inspect supported media providers and format capabilities.",
}


class InvalidScopeError(ValueError):
    """Raised when an unrecognized or disallowed permission scope is requested."""


def generate_api_key() -> tuple[str, str, str]:
    """Generate a high-entropy commercial API key.

    Uses secrets.token_urlsafe(32) providing 256 bits of cryptographic entropy.

    Returns:
        tuple of (raw_secret_key, key_prefix, key_hash)
    """
    token = secrets.token_urlsafe(32)
    raw_key = f"{KEY_PREFIX}{token}"
    prefix = raw_key[:16]
    key_hash = hash_api_key(raw_key)
    return raw_key, prefix, key_hash


def hash_api_key(raw_key: str) -> str:
    """Compute the SHA-256 cryptographic digest of an API key string."""
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


def verify_api_key(presented_key: str, stored_hash: str) -> bool:
    """Verify a presented API key against a stored SHA-256 hash in constant time."""
    if not presented_key or not stored_hash:
        return False
    computed_hash = hash_api_key(presented_key)
    return hmac.compare_digest(computed_hash, stored_hash)


def mask_api_key(prefix: str) -> str:
    """Format safe masked representation of an API key for logs and UI listings."""
    if len(prefix) >= 16:
        return f"{prefix}...****"
    return f"{prefix}****"


def validate_scopes(requested_scopes: Sequence[str]) -> list[str]:
    """Validate requested permission scopes against the supported catalog.

    Raises:
        InvalidScopeError if any requested scope is not recognized.
    """
    if not requested_scopes:
        return ["downloads:read"]

    cleaned_scopes = []
    for scope in requested_scopes:
        s = scope.strip().lower()
        if s not in VALID_API_SCOPES:
            raise InvalidScopeError(
                f"Invalid scope '{scope}'. Allowed scopes: {', '.join(sorted(VALID_API_SCOPES.keys()))}"
            )
        if s not in cleaned_scopes:
            cleaned_scopes.append(s)

    return cleaned_scopes
