"""GrabItDown download request and result models."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from src.models.enums import (
    AudioFormat,
    DownloadStatus,
    MediaType,
    ProviderType,
    Quality,
    VideoFormat,
)
from src.models.media import MediaInfo


def _format_filesize(size_bytes: Optional[int]) -> str:
    """Convert a byte size into a human-readable string."""
    if size_bytes is None:
        return "Unknown"

    n = float(size_bytes)
    if n < 1024:
        return f"{int(n)} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:.2f} MB"
    return f"{n / 1024**3:.2f} GB"


def _format_duration(seconds: Optional[int]) -> str:
    """Format a duration in seconds into a human-readable string."""
    if seconds is None:
        return "Unknown"

    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        minutes, sec = divmod(total, 60)
        return f"{minutes}m {sec}s"
    hours, remainder = divmod(total, 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours}h {minutes}m {sec}s"


def _format_speed(bytes_per_second: float) -> str:
    """Convert a speed in bytes per second to a human-readable string."""
    if bytes_per_second <= 0:
        return "0 B/s"

    n = float(bytes_per_second)
    if n < 1024:
        return f"{n:.0f} B/s"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB/s"
    if n < 1024**3:
        return f"{n / 1024**2:.2f} MB/s"
    return f"{n / 1024**3:.2f} GB/s"


class DownloadRequest(BaseModel):
    """Represents a single media download request."""

    url: str
    media_type: MediaType = MediaType.VIDEO
    quality: Quality = Quality.Q_1080P
    video_format: VideoFormat = VideoFormat.MP4
    audio_format: AudioFormat = AudioFormat.MP3
    audio_bitrate: str = "192k"
    output_directory: str | None = None
    filename: str | None = None
    embed_subtitles: bool = False
    subtitle_languages: list[str] = Field(default_factory=list)
    embed_thumbnail: bool = True

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """Validate that the URL uses HTTP or HTTPS."""
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("URL must start with 'http://' or 'https://'.")
        return value


class DownloadProgress(BaseModel):
    """Represents the current progress of a download job."""

    job_id: str
    status: DownloadStatus
    bytes_downloaded: int = 0
    total_bytes: int | None = None
    percent: float = 0.0
    speed_bytes_per_second: float = 0.0
    eta_seconds: int | None = None
    elapsed_seconds: float = 0.0
    current_file: str | None = None

    @property
    def speed_human(self) -> str:
        """Human-readable representation of download speed."""
        return _format_speed(self.speed_bytes_per_second)

    @property
    def eta_human(self) -> str:
        """Human-readable representation of remaining time."""
        return _format_duration(self.eta_seconds)

    @property
    def percent_display(self) -> str:
        """Formatted percentage string."""
        return f"{self.percent:.1f}%"


class DownloadResult(BaseModel):
    """Represents the final outcome of a download job."""

    job_id: str
    url: str
    provider: ProviderType
    status: DownloadStatus
    title: str
    file_path: str | None = None
    file_size_bytes: int | None = None
    media_type: MediaType
    quality: str | None = None
    format: str | None = None
    duration_seconds: int | None = None
    error_message: str | None = None
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def file_size_human(self) -> str:
        """Human-readable representation of the downloaded file size."""
        return _format_filesize(self.file_size_bytes)

    @property
    def download_duration(self) -> timedelta | None:
        """Return the time taken for the download if timestamps are available."""
        if self.started_at is None or self.completed_at is None:
            return None
        return self.completed_at - self.started_at

    @property
    def is_successful(self) -> bool:
        """Return True if the download completed successfully."""
        return self.status == DownloadStatus.COMPLETED


class DownloadJob(BaseModel):
    """Represents a tracked download job with lifecycle state."""

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    request: DownloadRequest
    progress: DownloadProgress
    result: DownloadResult | None = None
    media_info: MediaInfo | None = None
    priority: str = "normal"
    parent_job_id: str | None = None
    playlist_index: int | None = None
    title: str | None = None  # optional display title (e.g. from playlist item) before result
    user_id: str = "system"
    tenant_id: str = "default"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

