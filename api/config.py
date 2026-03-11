"""GrabItDown API configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


def _env_file_path() -> str | None:
    """Path to .env in project root (same as env_loader), so settings match regardless of cwd."""
    try:
        from src.env_loader import _project_root
        root = _project_root()
        path = root / ".env"
        return str(path) if path.is_file() else None
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

    # WebSocket
    ws_ping_interval: int = 30
    ws_max_connections: int = 100

    # Auth (skeleton — not enforced yet)
    secret_key: str = "grabitdown-dev-secret-change-in-production"
    access_token_expire_minutes: int = 60 * 24  # 24h
    algorithm: str = "HS256"

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
        """Synchronous database URL for Alembic."""
        return (
            self.database_url.replace("+asyncpg", "").replace("+aiosqlite", "")
        )


@lru_cache
def get_api_settings() -> APISettings:
    """Return cached API settings."""
    return APISettings()
