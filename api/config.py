"""GrabItDown API configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


def _env_file_path() -> str | None:
    """Path to .env in project root or deploy/.env, so settings match regardless of cwd."""
    try:
        from src.env_loader import _project_root
        root = _project_root()
        for candidate in (root / ".env", root / "deploy" / ".env"):
            if candidate.is_file():
                return str(candidate)
        return None
    except Exception:
        return None


class APISettings(BaseSettings):
    """API-specific settings.

    Loaded from environment variables with GID_API_ prefix.
    .env is loaded from project root (not cwd) so it is always found.
    """

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    workers: int = 1
    debug: bool = False

    # CORS
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    # Database — must be set in .env as GID_API_DATABASE_URL (e.g. postgresql+asyncpg://... or sqlite+aiosqlite:///...)
    database_url: str = Field(..., description="Set GID_API_DATABASE_URL in .env")
    migration_database_url: str | None = Field(
        default=None,
        validation_alias="GID_MIGRATION_DATABASE_URL",
        description="Alembic migration database URL (schema owner / DDL role: gid_user)",
    )
    database_schema: str = "gid"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout: int = 30
    database_echo: bool = False

    # API
    api_prefix: str = "/api/v1"

    # Rate limiting
    rate_limit_per_minute: int = 60
    rate_limit_burst: int = 10
    tenant_api_rate_limit_per_minute: int = 300
    api_key_rate_limit_default: int = 60

    # WebSocket
    ws_ping_interval: int = 30
    ws_max_connections: int = 100

    # Auth & JWT Security (STEP 02-B1)
    secret_key: str = Field(
        default="mediaforge-development-secret-key-minimum-32-chars-long!",
        validation_alias="GID_JWT_SECRET_KEY",
    )
    jwt_secret_key: str | None = Field(
        default=None,
        validation_alias="GID_JWT_SECRET_KEY",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15  # Short-lived (15 mins) as per specification
    jwt_issuer: str = "mediaforge"
    jwt_audience: str = "mediaforge-api"

    # Redis configuration
    redis_host: str = Field(default="127.0.0.1", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=26379, validation_alias="REDIS_PORT")
    redis_password: str | None = Field(default=None, validation_alias="REDIS_PASSWORD")
    redis_db: int = 0

    # SMTP (Mailpit) configuration
    smtp_host: str = Field(default="127.0.0.1", validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=51025, validation_alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, validation_alias="SMTP_USER")
    smtp_password: str | None = Field(default=None, validation_alias="SMTP_PASSWORD")
    smtp_from: str = "noreply@mediaforge.local"
    smtp_tls: bool = False

    # Registration & Email Verification (STEP 02-B2A)
    registration_token_expire_hours: int = 24
    registration_rate_limit_per_minute: int = 5
    registration_rate_limit_per_hour_email: int = 3
    resend_cooldown_seconds: int = 60

    # Sessions & Refresh Tokens (STEP 02-B2B)
    refresh_token_expire_days: int = 30
    login_rate_limit_per_minute: int = 10
    login_rate_limit_per_15min_email: int = 10
    rate_limit_fail_closed: bool = True

    # Password Recovery & Reset (STEP 02-B2C)
    password_reset_token_expire_minutes: int = 30
    password_reset_rate_limit_per_minute: int = 5
    password_reset_rate_limit_per_hour_email: int = 3

    # Test/dev: skip DB seed (default tenant/user) on startup
    skip_seed: bool = False

    # ffmpeg/ffprobe dir for YouTube (from .env or GID_FFMPEG_LOCATION; no GID_API_ prefix)
    ffmpeg_location: str | None = Field(default=None, validation_alias="GID_FFMPEG_LOCATION")

    model_config = {
        "env_prefix": "GID_API_",
        "env_file": _env_file_path() or ".env",  # project root .env so it's found regardless of cwd
        "extra": "ignore",  # allow other env vars (e.g. GID_FFMPEG_LOCATION) without declaring all
    }

    @property
    def sync_database_url(self) -> str:
        """Synchronous database URL for Alembic or sync drivers."""
        url = self.migration_database_url or self.database_url
        return (
            url.replace("+asyncpg", "").replace("+aiosqlite", "")
        )

    @property
    def sanitized_database_url(self) -> str:
        """Return runtime database URL with password masked."""
        from urllib.parse import urlparse, urlunparse
        try:
            parsed = urlparse(self.database_url)
            if parsed.password:
                netloc = f"{parsed.username}:***@{parsed.hostname}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                return urlunparse(parsed._replace(netloc=netloc))
            return self.database_url
        except Exception:
            return "***"


@lru_cache
def get_api_settings() -> APISettings:
    """Return cached API settings."""
    return APISettings()
