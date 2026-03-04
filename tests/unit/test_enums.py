"""Tests for GrabItDown enum definitions."""

from src.models.enums import (
    AudioFormat,
    DownloadStatus,
    MediaType,
    ProviderType,
    Quality,
    TranscriptFormat,
)


def test_media_type_values() -> None:
    """Verify MediaType enum values."""
    assert MediaType.VIDEO == "video"
    assert MediaType.AUDIO == "audio"


def test_download_status_values() -> None:
    """Verify all DownloadStatus values exist."""
    statuses = [s.value for s in DownloadStatus]
    assert "created" in statuses
    assert "downloading" in statuses
    assert "completed" in statuses
    assert "interrupted" in statuses
    assert "resuming" in statuses
    assert len(statuses) == 11


def test_quality_values() -> None:
    """Verify Quality enum values."""
    assert Quality.Q_1080P == "1080p"
    assert Quality.BEST == "best"


def test_provider_type_values() -> None:
    """Verify ProviderType enum values."""
    assert ProviderType.YOUTUBE == "youtube"
    assert ProviderType.FACEBOOK == "facebook"
    assert ProviderType.MEGA == "mega"


def test_audio_format_values() -> None:
    """Verify AudioFormat enum values."""
    assert AudioFormat.MP3 == "mp3"
    assert AudioFormat.FLAC == "flac"


def test_video_format_values() -> None:
    """Verify VideoFormat enum values."""
    from src.models.enums import VideoFormat

    assert VideoFormat.MP4 == "mp4"
    assert VideoFormat.WEBM == "webm"


def test_transcript_format_values() -> None:
    """Verify TranscriptFormat enum values."""
    assert TranscriptFormat.SRT == "srt"
    assert TranscriptFormat.JSON == "json"

