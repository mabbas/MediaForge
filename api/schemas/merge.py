"""API schemas for clip merging."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class MergeRequest(BaseModel):
    """Request to merge multiple clips."""

    clips: list[str] = Field(
        ...,
        min_length=2,
        max_length=20,
        description="List of file paths to merge (2-20 clips)",
        json_schema_extra={
            "examples": [
                [
                    "/path/clip1.mp4",
                    "/path/clip2.mp4",
                    "/path/clip3.mp4",
                ]
            ]
        },
    )
    output_format: str = Field(
        default="mp4",
        description="Output format (mp4, mkv, webm)",
    )
    mode: str = Field(
        default="auto",
        description="Merge mode: 'auto' (detect), 'concat' (fast, same codec), 'reencode' (mix any codecs)",
    )
    resolution: Optional[str] = Field(
        default=None,
        description="Force output resolution (e.g. 1920x1080)",
    )
    video_bitrate: Optional[str] = Field(
        default=None,
        description="Video bitrate (e.g. 4M)",
    )
    audio_bitrate: str = Field(
        default="192k",
        description="Audio bitrate",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "clips": [
                        "/downloads/clip1.mp4",
                        "/downloads/clip2.mp4",
                    ],
                    "mode": "auto",
                    "output_format": "mp4",
                }
            ]
        }
    }


class MergeResultResponse(BaseModel):
    """Response from clip merging."""

    success: bool
    merge_id: str
    clip_count: int = 0
    output_path: str = ""
    total_duration_seconds: float = 0.0
    file_size_bytes: int = 0
    file_size_human: str = ""
    created_at: str = ""
    clips_used: list[str] = []
    error: Optional[str] = None
