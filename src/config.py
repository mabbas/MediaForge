"""Application configuration management for GrabItDown."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "default.yaml"


def load_yaml_config(path: Path) -> Dict[str, Any]:
    """Load a YAML configuration file into a dictionary.

    Returns an empty dict if the file does not exist or is empty/invalid.
    """
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}

    if not isinstance(data, dict):
        return {}
    return data


_DEFAULT_CONFIG: Dict[str, Any] = load_yaml_config(DEFAULT_CONFIG_PATH)


def _section(name: str) -> Dict[str, Any]:
    """Return a section from the default config."""
    value = _DEFAULT_CONFIG.get(name, {})
    return value if isinstance(value, dict) else {}


class AppSettings(BaseModel):
    """Application-level settings."""

    name: str = "GrabItDown"
    version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_format: str = "text"


class DownloadSettings(BaseModel):
    """Download-related settings."""

    output_directory: str = "./downloads"
    filename_template: str = "{provider}/{channel}/{title}.{ext}"

    @field_validator("output_directory", mode="before")
    @classmethod
    def expand_output_directory(cls, v: str) -> str:
        """Expand ~ to user home so e.g. ~/Downloads becomes C:\\Users\\<user>\\Downloads."""
        if v and isinstance(v, str):
            return str(Path(v).expanduser())
        return v or "./downloads"
    max_concurrent_downloads: int = 3
    absolute_max_concurrent: int = 5
    queue_max_size: int = 100
    bandwidth_limit_per_download: int = 0
    total_bandwidth_limit: int = 0
    download_timeout_seconds: int = 3600
    connection_timeout_seconds: int = 30
    retry_max_attempts: int = 3
    retry_backoff_base: int = 2
    max_file_size_mb: int = 5000
    min_disk_space_mb: int = 500


class VideoSettings(BaseModel):
    """Video-specific settings."""

    default_quality: str = "1080p"
    preferred_format: str = "mp4"
    embed_subtitles: bool = True
    embed_thumbnail: bool = True
    write_metadata: bool = True


class AudioSettings(BaseModel):
    """Audio-specific settings."""

    default_format: str = "mp3"
    default_bitrate: str = "192k"
    embed_thumbnail: bool = True
    embed_metadata: bool = True


class TranscriptSettings(BaseModel):
    """Transcript-related settings."""

    default_languages: List[str] = Field(default_factory=lambda: ["en", "ur"])
    fallback_to_whisper: bool = True
    whisper_model: str = "medium"
    whisper_device: str = "auto"
    output_formats: List[str] = Field(default_factory=lambda: ["srt", "txt"])
    max_audio_duration_minutes: int = 180


class PlaylistSettings(BaseModel):
    """Playlist handling settings."""

    max_playlist_size: int = 500
    default_concurrency: int = 3
    skip_existing: bool = True
    numbering: bool = True
    reverse_order: bool = False


class ResumeSettings(BaseModel):
    """Download resume behavior settings."""

    enabled: bool = True
    part_file_extension: str = ".part"
    progress_save_interval_seconds: int = 3
    max_auto_retries: int = 5
    auto_resume_on_reconnect: bool = True
    scan_incomplete_on_start: bool = True
    max_part_file_age_days: int = 7


class ProviderYouTubeSettings(BaseModel):
    """YouTube provider settings."""

    enabled: bool = True
    cookies_file: Optional[str] = None
    geo_bypass: bool = True


class ProviderFacebookSettings(BaseModel):
    """Facebook provider settings."""

    enabled: bool = False
    cookies_file: Optional[str] = None


class ProviderMegaSettings(BaseModel):
    """Mega provider settings."""

    enabled: bool = False


class ProviderDropboxSettings(BaseModel):
    """Dropbox provider settings."""

    enabled: bool = False


class ProviderGenericSettings(BaseModel):
    """Generic provider settings."""

    enabled: bool = True


class ProvidersSettings(BaseModel):
    """Configuration for all supported providers."""

    youtube: ProviderYouTubeSettings = Field(
        default_factory=lambda: ProviderYouTubeSettings(**_section("providers").get("youtube", {}))
    )
    facebook: ProviderFacebookSettings = Field(
        default_factory=lambda: ProviderFacebookSettings(**_section("providers").get("facebook", {}))
    )
    mega: ProviderMegaSettings = Field(
        default_factory=lambda: ProviderMegaSettings(**_section("providers").get("mega", {}))
    )
    dropbox: ProviderDropboxSettings = Field(
        default_factory=lambda: ProviderDropboxSettings(**_section("providers").get("dropbox", {}))
    )
    generic: ProviderGenericSettings = Field(
        default_factory=lambda: ProviderGenericSettings(**_section("providers").get("generic", {}))
    )


class AppConfig(BaseSettings):
    """Top-level application configuration."""

    app: AppSettings = Field(default_factory=lambda: AppSettings(**_section("app")))
    download: DownloadSettings = Field(default_factory=lambda: DownloadSettings(**_section("download")))
    video: VideoSettings = Field(default_factory=lambda: VideoSettings(**_section("video")))
    audio: AudioSettings = Field(default_factory=lambda: AudioSettings(**_section("audio")))
    transcript: TranscriptSettings = Field(default_factory=lambda: TranscriptSettings(**_section("transcript")))
    playlist: PlaylistSettings = Field(default_factory=lambda: PlaylistSettings(**_section("playlist")))
    resume: ResumeSettings = Field(default_factory=lambda: ResumeSettings(**_section("resume")))
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)

    model_config = SettingsConfigDict(
        env_prefix="GID_",
        env_nested_delimiter="__",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> AppConfig:
    """Return the singleton AppConfig instance."""
    return AppConfig()

