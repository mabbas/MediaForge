"""GrabItDown transcript models."""

from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field, field_validator

from src.models.enums import TranscriptFormat, TranscriptSource


def _format_timestamp(total_seconds: float) -> str:
    """Format seconds with millisecond precision as HH:MM:SS.mmm."""
    total_ms = int(round(total_seconds * 1000))
    seconds, millis = divmod(total_ms, 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}.{millis:03d}"


class TranscriptSegment(BaseModel):
    """Represents a single transcript segment."""

    start_seconds: float
    end_seconds: float
    text: str

    @property
    def start_timestamp(self) -> str:
        """Return the start timestamp formatted as HH:MM:SS.mmm."""
        return _format_timestamp(self.start_seconds)

    @property
    def end_timestamp(self) -> str:
        """Return the end timestamp formatted as HH:MM:SS.mmm."""
        return _format_timestamp(self.end_seconds)

    @property
    def duration_seconds(self) -> float:
        """Return the duration of the segment in seconds."""
        return self.end_seconds - self.start_seconds


class TranscriptRequest(BaseModel):
    """Represents a request to generate or fetch a transcript."""

    url: str
    language: str = "en"
    output_format: TranscriptFormat = TranscriptFormat.SRT
    use_whisper: bool = False
    whisper_model: str = "medium"

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """Validate that the URL uses HTTP or HTTPS."""
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("URL must start with 'http://' or 'https://'.")
        return value

    @field_validator("language")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        """Validate the language code or wildcard."""
        if value == "*":
            return value
        if len(value) == 2 and value.islower():
            return value
        raise ValueError(
            "Language must be a 2-character ISO 639-1 code (e.g., 'en', 'ur')"
        )


class TranscriptResult(BaseModel):
    """Represents the result of transcript generation."""

    url: str
    language: str
    source: TranscriptSource
    output_format: TranscriptFormat
    segments: List[TranscriptSegment] = Field(default_factory=list)
    file_path: str | None = None
    full_text: str | None = None
    duration_seconds: float | None = None
    word_count: int | None = None
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def segment_count(self) -> int:
        """Return the number of transcript segments."""
        return len(self.segments)

