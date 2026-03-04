"""Tests for GrabItDown download models."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from src.models.download import DownloadJob, DownloadProgress, DownloadRequest, DownloadResult
from src.models.enums import (
    DownloadStatus,
    MediaType,
    ProviderType,
    Quality,
    AudioFormat,
    VideoFormat,
)


def test_download_request_valid_url() -> None:
    """HTTPS URL passes validation."""
    req = DownloadRequest(url="https://youtube.com/watch?v=test")
    assert req.url == "https://youtube.com/watch?v=test"


def test_download_request_http_url() -> None:
    """HTTP URL also passes validation."""
    req = DownloadRequest(url="http://example.com/video")
    assert req.url.startswith("http://")


def test_download_request_invalid_url() -> None:
    """URL without http/https fails validation."""
    with pytest.raises(ValidationError):
        DownloadRequest(url="not-a-url")


def test_download_request_ftp_url() -> None:
    """FTP URL fails validation."""
    with pytest.raises(ValidationError):
        DownloadRequest(url="ftp://files.com/video.mp4")


def test_download_request_defaults() -> None:
    """Verify all default values are correct."""
    req = DownloadRequest(url="https://youtube.com/watch?v=test")
    assert req.media_type == MediaType.VIDEO
    assert req.quality == Quality.Q_1080P
    assert req.video_format == VideoFormat.MP4
    assert req.audio_format == AudioFormat.MP3
    assert req.audio_bitrate == "192k"
    assert req.embed_thumbnail is True
    assert req.embed_subtitles is False
    assert req.subtitle_languages == []


def test_download_progress_speed_human_mbps() -> None:
    """5MB/s displays as '5.00 MB/s'."""
    p = DownloadProgress(
        job_id="test",
        status=DownloadStatus.DOWNLOADING,
        speed_bytes_per_second=5242880,
    )
    assert p.speed_human == "5.00 MB/s"


def test_download_progress_speed_human_kbps() -> None:
    """500KB/s displays correctly."""
    p = DownloadProgress(
        job_id="test",
        status=DownloadStatus.DOWNLOADING,
        speed_bytes_per_second=512000,
    )
    assert "KB/s" in p.speed_human


def test_download_progress_speed_human_zero() -> None:
    """Zero speed shows '0 B/s'."""
    p = DownloadProgress(
        job_id="test",
        status=DownloadStatus.DOWNLOADING,
        speed_bytes_per_second=0,
    )
    assert p.speed_human == "0 B/s"


def test_download_progress_eta_human() -> None:
    """150 seconds → '2m 30s'."""
    p = DownloadProgress(
        job_id="test",
        status=DownloadStatus.DOWNLOADING,
        eta_seconds=150,
    )
    assert p.eta_human == "2m 30s"


def test_download_progress_eta_human_none() -> None:
    """None ETA → 'Unknown'."""
    p = DownloadProgress(job_id="test", status=DownloadStatus.DOWNLOADING)
    assert p.eta_human == "Unknown"


def test_download_progress_percent_display() -> None:
    """63.5% displays as '63.5%'."""
    p = DownloadProgress(
        job_id="test",
        status=DownloadStatus.DOWNLOADING,
        percent=63.5,
    )
    assert p.percent_display == "63.5%"


def test_download_result_is_successful_true() -> None:
    """COMPLETED status → is_successful True."""
    r = DownloadResult(
        job_id="test",
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        status=DownloadStatus.COMPLETED,
        title="Test",
        media_type=MediaType.VIDEO,
    )
    assert r.is_successful is True


def test_download_result_is_successful_false() -> None:
    """FAILED status → is_successful False."""
    r = DownloadResult(
        job_id="test",
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        status=DownloadStatus.FAILED,
        title="Test",
        media_type=MediaType.VIDEO,
    )
    assert r.is_successful is False


def test_download_result_file_size_human() -> None:
    """File size formatted correctly."""
    r = DownloadResult(
        job_id="test",
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        status=DownloadStatus.COMPLETED,
        title="Test",
        media_type=MediaType.VIDEO,
        file_size_bytes=1073741824,
    )
    assert r.file_size_human == "1.00 GB"


def test_download_result_download_duration() -> None:
    """Duration calculated from timestamps."""
    start = datetime(2026, 1, 1, 10, 0, 0)
    end = datetime(2026, 1, 1, 10, 5, 30)
    r = DownloadResult(
        job_id="test",
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        status=DownloadStatus.COMPLETED,
        title="Test",
        media_type=MediaType.VIDEO,
        started_at=start,
        completed_at=end,
    )
    assert r.download_duration == timedelta(minutes=5, seconds=30)


def test_download_result_download_duration_none() -> None:
    """None timestamps → None duration."""
    r = DownloadResult(
        job_id="test",
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        status=DownloadStatus.COMPLETED,
        title="Test",
        media_type=MediaType.VIDEO,
    )
    assert r.download_duration is None


def test_download_job_auto_id() -> None:
    """Job ID auto-generated as UUID."""
    j = DownloadJob(
        request=DownloadRequest(url="https://youtube.com/watch?v=test"),
        progress=DownloadProgress(job_id="placeholder", status=DownloadStatus.CREATED),
    )
    assert len(j.job_id) == 36
    assert "-" in j.job_id


def test_download_job_defaults() -> None:
    """Default user_id and tenant_id."""
    j = DownloadJob(
        request=DownloadRequest(url="https://youtube.com/watch?v=test"),
        progress=DownloadProgress(job_id="test", status=DownloadStatus.CREATED),
    )
    assert j.user_id == "system"
    assert j.tenant_id == "default"
    assert j.priority == "normal"
    assert j.parent_job_id is None

