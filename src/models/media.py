"""GrabItDown media information models."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from src.models.enums import ProviderType, Quality


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


class Thumbnail(BaseModel):
    """Represents a media thumbnail image."""

    url: str
    width: int | None = None
    height: int | None = None
    quality: str | None = None


class MediaFormat(BaseModel):
    """Represents an available media encoding/format."""

    format_id: str
    extension: str
    quality: str | None = None
    resolution: str | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize_bytes: int | None = None
    filesize_approx_bytes: int | None = None
    bitrate: float | None = None
    has_video: bool = True
    has_audio: bool = True
    note: str | None = None

    @property
    def filesize_human(self) -> str:
        """Human-readable representation of the file size."""
        size = self.filesize_bytes or self.filesize_approx_bytes
        return _format_filesize(size)

    @property
    def is_video_only(self) -> bool:
        """Return True if the format contains only video."""
        return self.has_video and not self.has_audio

    @property
    def is_audio_only(self) -> bool:
        """Return True if the format contains only audio."""
        return self.has_audio and not self.has_video


class MediaInfo(BaseModel):
    """Aggregated metadata for a media item."""

    url: str
    provider: ProviderType
    media_id: str
    title: str
    description: str | None = None
    duration_seconds: int | None = None
    channel_name: str | None = None
    channel_url: str | None = None
    upload_date: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    thumbnails: List[Thumbnail] = Field(default_factory=list)
    formats: List[MediaFormat] = Field(default_factory=list)
    subtitles_available: Dict[str, List[str]] = Field(default_factory=dict)
    is_live: bool = False
    is_playlist: bool = False

    @property
    def duration_human(self) -> str:
        """Return a human-readable representation of the media duration."""
        return _format_duration(self.duration_seconds)

    @property
    def best_thumbnail(self) -> Thumbnail | None:
        """Return the best thumbnail by area or the last one if dimensions are missing."""
        if not self.thumbnails:
            return None

        with_area: List[tuple[int, Thumbnail]] = []
        without_area: List[Thumbnail] = []
        for thumb in self.thumbnails:
            if thumb.width is not None and thumb.height is not None:
                with_area.append((thumb.width * thumb.height, thumb))
            else:
                without_area.append(thumb)

        if with_area:
            return max(with_area, key=lambda x: x[0])[1]
        return self.thumbnails[-1]

    def get_formats_by_quality(self, quality: Quality) -> List[MediaFormat]:
        """Return formats where the quality value appears in quality or resolution fields."""
        value = quality.value
        results: List[MediaFormat] = []
        for fmt in self.formats:
            q_field = fmt.quality or ""
            r_field = fmt.resolution or ""
            if value in q_field or value in r_field:
                results.append(fmt)
        return results

    def get_audio_formats(self) -> List[MediaFormat]:
        """Return all audio-only formats."""
        return [f for f in self.formats if f.has_audio and not f.has_video]

    def get_video_formats(self) -> List[MediaFormat]:
        """Return all formats that contain video."""
        return [f for f in self.formats if f.has_video]

    def has_subtitles(self, language: str) -> bool:
        """Return True if subtitles are available for the given language."""
        return language in self.subtitles_available

