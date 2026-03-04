"""GrabItDown playlist models."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, field_validator

from src.models.enums import MediaType, ProviderType, Quality, VideoFormat, AudioFormat


def _format_duration(seconds: int | None) -> str:
    """Format seconds into a human-readable duration string."""
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


class PlaylistItem(BaseModel):
    """Represents a single media item within a playlist."""

    index: int
    url: str
    title: str
    media_id: str
    duration_seconds: int | None = None
    channel_name: str | None = None
    thumbnail_url: str | None = None
    is_available: bool = True

    @property
    def duration_human(self) -> str:
        """Return a human-readable representation of the item duration."""
        return _format_duration(self.duration_seconds)


class PlaylistInfo(BaseModel):
    """Describes a playlist and its items."""

    url: str
    provider: ProviderType
    playlist_id: str
    title: str
    description: str | None = None
    channel_name: str | None = None
    item_count: int = 0
    items: List[PlaylistItem] = Field(default_factory=list)
    thumbnail_url: str | None = None

    @property
    def total_duration_seconds(self) -> int:
        """Return the sum of all item durations, skipping unknown values."""
        return sum(item.duration_seconds or 0 for item in self.items)

    @property
    def total_duration_human(self) -> str:
        """Return human-readable representation of the total duration."""
        return _format_duration(self.total_duration_seconds)

    @property
    def available_items(self) -> List[PlaylistItem]:
        """Return only items that are available."""
        return [item for item in self.items if item.is_available]

    def get_items_by_range(self, start: int, end: int) -> List[PlaylistItem]:
        """Return a slice of items using standard list slicing semantics."""
        return self.items[start:end]


class PlaylistDownloadRequest(BaseModel):
    """Represents a request to download a playlist."""

    url: str
    media_type: MediaType = MediaType.VIDEO
    quality: Quality = Quality.Q_1080P
    video_format: VideoFormat = VideoFormat.MP4
    audio_format: AudioFormat = AudioFormat.MP3
    audio_bitrate: str = "192k"
    items: list[int] | str = "all"
    concurrency: int = 3
    skip_existing: bool = True
    output_directory: str | None = None
    embed_subtitles: bool = False
    subtitle_languages: list[str] = Field(default_factory=list)

    @field_validator("concurrency")
    @classmethod
    def _validate_concurrency(cls, value: int) -> int:
        """Ensure concurrency is between 1 and 5, inclusive."""
        if not (1 <= value <= 5):
            raise ValueError("concurrency must be between 1 and 5.")
        return value

    @field_validator("items")
    @classmethod
    def _validate_items(cls, value: list[int] | str) -> list[int] | str:
        """Validate the items selection."""
        if isinstance(value, str):
            if value != "all":
                raise ValueError("items must be 'all' when provided as a string.")
            return value

        if not value:
            raise ValueError("items list must not be empty.")
        if any(not isinstance(v, int) or v <= 0 for v in value):
            raise ValueError("items list must contain positive integers.")
        return value

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """Validate that the URL uses HTTP or HTTPS."""
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("URL must start with 'http://' or 'https://'.")
        return value

