"""Pydantic schemas for tenant registration and email verification (STEP 02-B2A)."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
SLUG_REGEX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PASSWORD_UPPER_REGEX = re.compile(r"[A-Z]")
PASSWORD_LOWER_REGEX = re.compile(r"[a-z]")
PASSWORD_DIGIT_REGEX = re.compile(r"[0-9]")
PASSWORD_SPECIAL_REGEX = re.compile(r"""[!@#$%^&*()_+\-=\[\]{}|;:'",.<>?/\\`~]""")


def validate_password_complexity(v: str) -> str:
    """Validate that a password meets complexity rules (8-128 chars, upper, lower, digit, special)."""
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if len(v) > 128:
        raise ValueError("Password cannot exceed 128 characters")
    if not PASSWORD_UPPER_REGEX.search(v):
        raise ValueError("Password must contain at least one uppercase letter")
    if not PASSWORD_LOWER_REGEX.search(v):
        raise ValueError("Password must contain at least one lowercase letter")
    if not PASSWORD_DIGIT_REGEX.search(v):
        raise ValueError("Password must contain at least one digit")
    if not PASSWORD_SPECIAL_REGEX.search(v):
        raise ValueError("Password must contain at least one special character")
    return v


class RegisterRequest(BaseModel):
    """Payload for self-service tenant and owner registration."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., max_length=320, description="User email address (normalized to lowercase)")
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Owner password (8-128 characters, requires upper, lower, digit, special)",
    )
    tenant_name: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Display name of the organization / tenant",
    )
    tenant_slug: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=50,
        description="Optional URL slug for tenant (kebab-case, lowercase alphanumeric and hyphens)",
    )
    display_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Optional display name for the user",
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError("Invalid email format")
        return cleaned

    @field_validator("tenant_name")
    @classmethod
    def sanitize_tenant_name(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Tenant name must be at least 2 characters")
        return cleaned

    @field_validator("tenant_slug")
    @classmethod
    def validate_slug(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        slug = v.strip().lower()
        if not SLUG_REGEX.match(slug):
            raise ValueError(
                "Tenant slug must contain only lowercase alphanumeric characters and hyphens, "
                "and cannot start or end with a hyphen"
            )
        return slug

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        return validate_password_complexity(v)


class RegisterResponse(BaseModel):
    """Generic registration response preventing user/tenant enumeration."""

    model_config = ConfigDict(extra="forbid")

    message: str = "Registration initiated. If this email is eligible, a verification link has been sent."
    email: str


class VerifyEmailRequest(BaseModel):
    """Payload to verify an email address using the raw token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=16, max_length=128, description="Verification token from email")

    @field_validator("token")
    @classmethod
    def clean_token(cls, v: str) -> str:
        return v.strip()


class VerifyEmailResponse(BaseModel):
    """Response returned upon successful email verification and account activation."""

    model_config = ConfigDict(extra="forbid")

    message: str = "Email verified successfully. You may now log in."
    tenant_id: str
    tenant_slug: str
    user_id: str
    email: str


class ResendVerificationRequest(BaseModel):
    """Payload to request a new verification email."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., max_length=320, description="Email address associated with pending registration")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError("Invalid email format")
        return cleaned


class ResendVerificationResponse(BaseModel):
    """Generic response for resend verification endpoint."""

    model_config = ConfigDict(extra="forbid")

    message: str = "If a pending registration exists for this email, a new verification link has been sent."
