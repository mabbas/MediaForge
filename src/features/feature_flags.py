"""Feature flag configuration models for GrabItDown."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping

import yaml
from pydantic import BaseModel

# This file lives in src/features/, so we need to go two levels up
# to reach the project root that contains the config/ directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
FEATURE_FLAGS_PATH = CONFIG_DIR / "feature_flags.yaml"


def _load_yaml(path: Path) -> Mapping[str, Any]:
    """Load YAML data from the given path, returning a mapping."""
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


class VideoDownloadFeature(BaseModel):
    """Video download feature configuration."""

    enabled: bool
    max_quality: str
    max_file_size_mb: int
    daily_limit: int


class AudioDownloadFeature(BaseModel):
    """Audio download feature configuration."""

    enabled: bool
    formats: List[str]
    max_bitrate: str


class PlaylistDownloadFeature(BaseModel):
    """Playlist download feature configuration."""

    enabled: bool
    max_playlist_size: int


class BatchDownloadFeature(BaseModel):
    """Batch download feature configuration."""

    enabled: bool
    max_urls: int


class ConcurrentDownloadsFeature(BaseModel):
    """Concurrent downloads feature configuration."""

    enabled: bool
    max_value: int


class MultiConnectionFeature(BaseModel):
    """Multi-connection download feature configuration."""

    enabled: bool
    max_connections: int


class TranscriptYouTubeFeature(BaseModel):
    """YouTube transcript feature configuration."""

    enabled: bool
    languages: List[str]


class TranscriptWhisperFeature(BaseModel):
    """Whisper transcript feature configuration."""

    enabled: bool
    languages: List[str]
    model: str


class ProvidersFeature(BaseModel):
    """Provider availability feature configuration."""

    youtube: bool
    facebook: bool
    mega: bool
    dropbox: bool
    generic: bool


class ResumeDownloadFeature(BaseModel):
    """Resume download feature configuration."""

    enabled: bool


class DownloadHistoryFeature(BaseModel):
    """Download history feature configuration."""

    enabled: bool
    retention_days: int


class BandwidthControlFeature(BaseModel):
    """Bandwidth control feature configuration."""

    enabled: bool


class ApiAccessFeature(BaseModel):
    """API access feature configuration."""

    enabled: bool
    rate_limit_per_hour: int


class FilenameTemplateFeature(BaseModel):
    """Filename template customization feature configuration."""

    enabled: bool


class EmbedSubtitlesFeature(BaseModel):
    """Subtitle embedding feature configuration."""

    enabled: bool


class PriorityQueueFeature(BaseModel):
    """Priority queue feature configuration."""

    enabled: bool


class TierFeatures(BaseModel):
    """All feature flags for a given tier."""

    video_download: VideoDownloadFeature
    audio_download: AudioDownloadFeature
    playlist_download: PlaylistDownloadFeature
    batch_download: BatchDownloadFeature
    concurrent_downloads: ConcurrentDownloadsFeature
    multi_connection: MultiConnectionFeature
    transcript_youtube: TranscriptYouTubeFeature
    transcript_whisper: TranscriptWhisperFeature
    providers: ProvidersFeature
    resume_download: ResumeDownloadFeature
    download_history: DownloadHistoryFeature
    bandwidth_control: BandwidthControlFeature
    api_access: ApiAccessFeature
    filename_template: FilenameTemplateFeature
    embed_subtitles: EmbedSubtitlesFeature
    priority_queue: PriorityQueueFeature
    storage_retention_hours: int


class TierConfig(BaseModel):
    """Configuration for a single subscription tier."""

    display_name: str
    price_monthly: int
    features: TierFeatures


class FeatureFlagsConfig(BaseModel):
    """Top-level feature flags configuration."""

    mode: str
    personal_mode_tier: str
    tiers: Dict[str, TierConfig]


@lru_cache(maxsize=1)
def load_feature_flags() -> FeatureFlagsConfig:
    """Load and cache the feature flags configuration."""
    data = _load_yaml(FEATURE_FLAGS_PATH)
    return FeatureFlagsConfig(**data)

