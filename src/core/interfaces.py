"""GrabItDown core interfaces and protocols."""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from pydantic import BaseModel

from src.models.download import DownloadProgress
from src.models.enums import Quality


@runtime_checkable
class ProgressCallback(Protocol):
    """Protocol for download progress callbacks."""

    def __call__(self, progress: DownloadProgress) -> None:  # pragma: no cover - protocol definition
        ...


class ProviderCapabilities(BaseModel):
    """Describes what a provider can do."""

    supports_video: bool = True
    supports_audio: bool = True
    supports_playlists: bool = False
    supports_subtitles: bool = False
    supports_live_streams: bool = False
    supports_formats_selection: bool = True
    supports_resume: bool = True
    max_quality: Quality = Quality.BEST
    supported_domains: List[str] = []

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "supports_video": True,
                    "supports_playlists": True,
                    "supported_domains": ["youtube.com", "youtu.be"],
                }
            ]
        }
    }

