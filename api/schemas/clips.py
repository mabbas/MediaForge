"""API schemas for clip operations."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ClipExtractRequest(BaseModel):
    """Request to extract a clip."""

    source: str = Field(
        ...,
        description="File path of downloaded video OR URL to download and clip",
        json_schema_extra={
            "examples": [
                "/path/to/video.mp4",
                "https://youtube.com/watch?v=dQw4w9WgXcQ",
            ]
        },
    )
    start_time: str = Field(
        ...,
        description="Start time (HH:MM:SS, MM:SS, or seconds)",
        json_schema_extra={
            "examples": ["00:01:30", "1:30", "90"],
        },
    )
    end_time: str = Field(
        ...,
        description="End time (HH:MM:SS, MM:SS, or seconds)",
        json_schema_extra={
            "examples": ["00:03:45", "3:45", "225"],
        },
    )
    output_format: str = Field(
        default="mp4",
        description="Output format (mp4, mkv, webm)",
    )
    mode: str = Field(
        default="precise",
        description="Cut mode: 'fast' (keyframe copy, instant) or 'precise' (re-encode, frame-accurate)",
    )
    resolution: Optional[str] = Field(
        default=None,
        description="Output resolution (e.g. 1280x720)",
    )
    video_bitrate: Optional[str] = Field(
        default=None,
        description="Video bitrate (e.g. 2M)",
    )
    audio_bitrate: str = Field(
        default="192k",
        description="Audio bitrate",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "source": "https://youtube.com/watch?v=abc",
                    "start_time": "00:01:00",
                    "end_time": "00:02:30",
                    "mode": "precise",
                    "output_format": "mp4",
                }
            ]
        }
    }


class ClipResultResponse(BaseModel):
    """Response from clip extraction."""

    success: bool
    clip_id: str
    source: str
    output_path: str = ""
    start_time: str
    end_time: str
    duration_seconds: float = 0.0
    file_size_bytes: int = 0
    file_size_human: str = ""
    created_at: str = ""
    error: Optional[str] = None


class ClipValidateRequest(BaseModel):
    """Request to validate timestamps."""

    start_time: str
    end_time: str
    source: Optional[str] = Field(
        default=None,
        description="Optional source file to check duration against",
    )


class ClipValidateResponse(BaseModel):
    """Timestamp validation response."""

    valid: bool
    error: str = ""
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    duration_seconds: float = 0.0
