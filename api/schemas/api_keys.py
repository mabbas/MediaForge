"""Pydantic schemas for API key creation, listing, rotation, and revocation (STEP 02-C2)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CreateAPIKeyRequest(BaseModel):
    """Payload for creating a new commercial API key."""

    name: str = Field(..., min_length=1, max_length=200, description="Descriptive name for the API key")
    scopes: list[str] = Field(
        default_factory=lambda: ["downloads:read"],
        description="Explicit permission scopes granted to this key",
    )
    expires_in_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=365,
        description="Optional lifetime in days. If null, key does not expire automatically.",
    )
    rate_limit: Optional[int] = Field(
        default=60,
        ge=1,
        le=1000,
        description="Per-key request limit per minute",
    )


class CreateAPIKeyResponse(BaseModel):
    """Response returned upon creation containing the raw secret key exactly once."""

    id: str
    name: str
    prefix: str
    key: str = Field(..., description="Raw plaintext API key. Displayed ONLY ONCE.")
    scopes: list[str]
    rate_limit: int
    expires_at: Optional[datetime] = None
    created_at: datetime


class APIKeyResponse(BaseModel):
    """Public metadata representation of an API key without sensitive secret."""

    id: str
    name: str
    prefix: str
    masked_key: str
    scopes: list[str]
    rate_limit: int
    is_active: bool
    is_revoked: bool
    revoked_at: Optional[datetime] = None
    revocation_reason: Optional[str] = None
    rotated_to_id: Optional[str] = None
    total_requests: int
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime


class APIKeyListResponse(BaseModel):
    """List of API keys belonging to the authenticated tenant."""

    keys: list[APIKeyResponse]
    total: int


class RotateAPIKeyRequest(BaseModel):
    """Payload for rotating an existing API key."""

    overlap_minutes: Optional[int] = Field(
        default=0,
        ge=0,
        le=1440,
        description="Grace period in minutes before old key is fully revoked. 0 = immediate revocation.",
    )


class RevokeAPIKeyResponse(BaseModel):
    """Response upon successful API key revocation."""

    message: str
    id: str
    revoked_at: datetime
