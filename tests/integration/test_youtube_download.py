"""Integration tests for YouTube downloads."""

from __future__ import annotations

import os
import shutil

import pytest

from src.models import (
    AudioFormat,
    DownloadRequest,
    DownloadStatus,
    MediaType,
    Quality,
    VideoFormat,
)
from src.exceptions import DownloadError

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


@pytest.fixture(scope="module")
def requires_ffmpeg() -> None:
    """Skip download tests if ffmpeg/ffprobe are not available."""
    ffmpeg_dir = os.environ.get("GID_FFMPEG_LOCATION", "").strip()
    if ffmpeg_dir and os.path.isdir(ffmpeg_dir):
        return
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    pytest.skip(
        "ffmpeg/ffprobe not found. Add them to PATH or set GID_FFMPEG_LOCATION "
        "to the directory containing ffmpeg (e.g. C:\\ffmpeg\\bin)."
    )


def test_download_audio_mp3(
    youtube_provider, test_video_url, tmp_path, requires_ffmpeg
) -> None:
    """Download audio as MP3 from real YouTube video."""
    request = DownloadRequest(
        url=test_video_url,
        media_type=MediaType.AUDIO,
        audio_format=AudioFormat.MP3,
        audio_bitrate="128k",
        output_directory=str(tmp_path),
    )

    try:
        result = youtube_provider.download(request, output_dir=str(tmp_path))
    except DownloadError as exc:
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            pytest.skip(f"YouTube test video not accessible: {msg}")
        if "ffmpeg" in msg.lower() and "not found" in msg.lower():
            pytest.skip(f"ffmpeg not available: {msg}. Set GID_FFMPEG_LOCATION.")
        if "timed out" in msg.lower():
            pytest.skip(f"Download timed out (network slow): {msg}")
        raise

    assert result.status == DownloadStatus.COMPLETED
    assert result.is_successful is True
    assert result.file_path is not None
    assert os.path.exists(result.file_path)
    assert os.path.getsize(result.file_path) > 0
    assert result.title
    assert result.started_at is not None
    assert result.completed_at is not None

    print(f"\n  Title: {result.title}")
    print(f"  File: {result.file_path}")
    print(f"  Size: {result.file_size_human}")
    print(f"  Duration: {result.download_duration}")


def test_download_video_low_quality(
    youtube_provider, test_video_url, tmp_path, requires_ffmpeg
) -> None:
    """Download video at 360p (lowest quality, fastest test)."""
    request = DownloadRequest(
        url=test_video_url,
        media_type=MediaType.VIDEO,
        quality=Quality.Q_360P,
        video_format=VideoFormat.MP4,
        output_directory=str(tmp_path),
    )

    try:
        result = youtube_provider.download(request, output_dir=str(tmp_path))
    except DownloadError as exc:
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            pytest.skip(f"YouTube test video not accessible: {msg}")
        if "ffmpeg" in msg.lower() and "not found" in msg.lower():
            pytest.skip(f"ffmpeg not available: {msg}. Set GID_FFMPEG_LOCATION.")
        if "timed out" in msg.lower():
            pytest.skip(f"Download timed out (network slow): {msg}")
        raise

    assert result.status == DownloadStatus.COMPLETED
    assert result.is_successful is True
    assert result.file_path is not None
    assert os.path.exists(result.file_path)

    file_size = os.path.getsize(result.file_path)
    assert file_size > 0

    print(f"\n  Title: {result.title}")
    print(f"  File: {result.file_path}")
    print(f"  Size: {file_size} bytes ({result.file_size_human})")


def test_download_with_progress_callback(
    youtube_provider, test_video_url, tmp_path, requires_ffmpeg
) -> None:
    """Progress callback receives updates."""
    progress_updates = []

    def on_progress(progress) -> None:
        progress_updates.append(progress)

    request = DownloadRequest(
        url=test_video_url,
        media_type=MediaType.AUDIO,
        audio_format=AudioFormat.MP3,
        audio_bitrate="128k",
        output_directory=str(tmp_path),
    )

    try:
        result = youtube_provider.download(
            request,
            output_dir=str(tmp_path),
            progress_callback=on_progress,
        )
    except DownloadError as exc:
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            pytest.skip(f"YouTube test video not accessible: {msg}")
        if "ffmpeg" in msg.lower() and "not found" in msg.lower():
            pytest.skip(f"ffmpeg not available: {msg}. Set GID_FFMPEG_LOCATION.")
        if "timed out" in msg.lower():
            pytest.skip(f"Download timed out (network slow): {msg}")
        raise

    assert result.is_successful is True
    assert len(progress_updates) > 0

    for p in progress_updates:
        assert p.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING)

    print(f"\n  Progress updates: {len(progress_updates)}")
    if progress_updates:
        last = progress_updates[-1]
        print(f"  Last update: {last.percent_display} at {last.speed_human}")


def test_download_invalid_url(youtube_provider, tmp_path) -> None:
    """Download with invalid URL raises DownloadError."""
    request = DownloadRequest(
        url="https://youtube.com/watch?v=INVALID_NOT_EXIST_XYZ",
        media_type=MediaType.AUDIO,
        audio_format=AudioFormat.MP3,
        output_directory=str(tmp_path),
    )

    with pytest.raises(DownloadError):
        youtube_provider.download(request, output_dir=str(tmp_path))

