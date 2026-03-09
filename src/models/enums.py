"""Enum definitions for GrabItDown domain concepts."""

from __future__ import annotations

from enum import Enum


class MediaType(str, Enum):
    """Supported media types."""

    VIDEO = "video"
    AUDIO = "audio"


class Quality(str, Enum):
    """Supported video quality labels."""

    Q_2160P = "2160p"
    Q_1440P = "1440p"
    Q_1080P = "1080p"
    Q_720P = "720p"
    Q_480P = "480p"
    Q_360P = "360p"
    BEST = "best"
    WORST = "worst"


class AudioFormat(str, Enum):
    """Supported audio formats."""

    MP3 = "mp3"
    M4A = "m4a"
    OPUS = "opus"
    FLAC = "flac"
    WAV = "wav"


class VideoFormat(str, Enum):
    """Supported video container formats."""

    MP4 = "mp4"
    MKV = "mkv"
    WEBM = "webm"


class DownloadStatus(str, Enum):
    """Lifecycle states for a download."""

    CREATED = "created"
    DEFERRED = "deferred"  # Added to queue but not started (waiting for Start)
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class ProviderType(str, Enum):
    """Supported provider types."""

    YOUTUBE = "youtube"
    FACEBOOK = "facebook"
    MEGA = "mega"
    DROPBOX = "dropbox"
    GENERIC = "generic"


class TranscriptFormat(str, Enum):
    """Supported transcript output formats."""

    SRT = "srt"
    VTT = "vtt"
    TXT = "txt"
    JSON = "json"


class TranscriptSource(str, Enum):
    """Sources for transcript data."""

    YOUTUBE_CC = "youtube_cc"
    WHISPER = "whisper"

