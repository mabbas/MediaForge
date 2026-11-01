"""Authentication schemas for login, refresh, session management and logout (STEP 02-B1 & STEP 02-B2B)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    """User login credentials."""

    email: str = Field(
        ...,
        max_length=320,
        description="User email address",
        examples=["system@grabitdown.local"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Account password",
    )
    tenant_slug: str | None = Field(
        default=None,
        max_length=100,
        description="Optional tenant slug. Defaults to 'default' tenant.",
    )


class TokenResponse(BaseModel):
    """Access token and refresh token response schema."""

    access_token: str = Field(..., description="Signed JWT access token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token lifetime in seconds")
    refresh_token: str | None = Field(default=None, description="Rotating opaque refresh token")
    session_id: str | None = Field(default=None, description="Active session identifier")


class RefreshRequest(BaseModel):
    """Request payload to rotate refresh token and issue new access token."""

    refresh_token: str = Field(..., min_length=32, description="Active rotating refresh token")


class LogoutRequest(BaseModel):
    """Optional parameters for logout."""

    all_sessions: bool = Field(
        default=False,
        description="If True, revokes all active sessions for the current user across all devices.",
    )


class LogoutResponse(BaseModel):
    """Logout outcome response."""

    message: str = "Successfully logged out."
    revoked_sessions: int = 1


class UserMeResponse(BaseModel):
    """Safe authenticated principal summary (no secrets or hashes)."""

    user_id: str
    tenant_id: str
    session_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    role: str
    tier: str
    auth_method: str = "jwt"


class ForgotPasswordRequest(BaseModel):
    """Payload to request password recovery."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., max_length=320, description="Account email address")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        import re
        cleaned = v.strip().lower()
        if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", cleaned):
            raise ValueError("Invalid email format")
        return cleaned


class ForgotPasswordResponse(BaseModel):
    """Generic response to prevent account enumeration."""

    message: str = "If the account exists and is eligible, a password reset link has been sent to your email."


class ResetPasswordRequest(BaseModel):
    """Payload to reset password using recovery token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=16, max_length=256, description="Single-use password recovery token")
    new_password: str = Field(..., min_length=8, max_length=128, description="New account password")

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        from api.schemas.registration import validate_password_complexity
        return validate_password_complexity(v)


class ResetPasswordResponse(BaseModel):
    """Password reset confirmation."""

    message: str = "Password has been successfully reset. Please log in with your new credentials."


class ChangePasswordRequest(BaseModel):
    """Payload for authenticated password change."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., min_length=1, max_length=128, description="Current account password")
    new_password: str = Field(..., min_length=8, max_length=128, description="New account password")

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        from api.schemas.registration import validate_password_complexity
        return validate_password_complexity(v)


class ChangePasswordResponse(BaseModel):
    """Authenticated password change confirmation."""

    message: str = "Password has been successfully changed. All existing sessions have been terminated. Please log in again."
