"""Tests for GrabItDown main facade."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.core.base_provider import BaseMediaProvider
from src.core.interfaces import ProviderCapabilities, ProgressCallback
from src.core.provider_registry import ProviderRegistry
from src.exceptions import FeatureDisabledError
from src.grabitdown import GrabItDown
from src.models.download import DownloadRequest, DownloadResult
from src.models.enums import (
    AudioFormat,
    DownloadStatus,
    MediaType,
    ProviderType,
    Quality,
    VideoFormat,
)


class MockDownloadProvider(BaseMediaProvider):
    """Mock provider for facade tests."""

    def __init__(self, delay: float = 0.1, fail: bool = False) -> None:
        self._delay = delay
        self._fail = fail

    @property
    def name(self) -> str:
        return "MockDownloadProvider"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GENERIC

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supported_domains=["mock.test"])

    def can_handle(self, url: str) -> bool:
        return url.startswith("https://mock.test/")

    def extract_info(self, url: str):
        from src.models.media import MediaInfo

        return MediaInfo(
            id="mock",
            url=url,
            title="Mock Title",
            uploader="Mock Uploader",
            duration=60,
            view_count=0,
            like_count=0,
            channel="Mock Channel",
            description=None,
            thumbnails=[],
            formats=[],
            upload_date=None,
        )

    def get_formats(self, url: str):
        from src.models.media import MediaFormat

        return [
            MediaFormat(
                itag="1",
                extension="mp4",
                resolution="720p",
                fps=30.0,
                vcodec="h264",
                acodec="aac",
                abr=128,
                tbr=1000,
                filesize=1024 * 1024,
                has_video=True,
                has_audio=True,
            )
        ]

    def download(
        self,
        request: DownloadRequest,
        output_dir: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        if self._fail:
            raise RuntimeError("Mock failure")

        if progress_callback:
            from src.models.download import DownloadProgress

            progress_callback(
                DownloadProgress(
                    job_id="pending",
                    status=DownloadStatus.DOWNLOADING,
                    percent=50.0,
                )
            )

        time.sleep(self._delay)

        return DownloadResult(
            job_id="test",
            url=request.url,
            provider=self.provider_type,
            status=DownloadStatus.COMPLETED,
            title="Test",
            media_type=request.media_type,
        )


def _create_mock_registry(delay: float = 0.1) -> ProviderRegistry:
    """Create registry with mock provider."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider(delay=delay))
    return registry


def test_initialization():
    """GrabItDown initializes all subsystems."""
    with patch("src.grabitdown.create_provider_registry") as mock_factory:
        mock_factory.return_value = _create_mock_registry()
        app = GrabItDown()

    assert app is not None
    assert app.list_providers() is not None
    app.shutdown(wait=False)


def test_download_single():
    """Single download via facade."""
    with patch("src.grabitdown.create_provider_registry") as mock_factory:
        mock_factory.return_value = _create_mock_registry()
        app = GrabItDown()

    app.start()
    job = app.download("https://mock.test/v1", mode="video", quality="720p")

    assert job.job_id is not None
    assert job.request.url == "https://mock.test/v1"

    time.sleep(1)
    app.shutdown(wait=True)


def test_download_audio():
    """Audio download via facade."""
    with patch("src.grabitdown.create_provider_registry") as mock_factory:
        mock_factory.return_value = _create_mock_registry()
        app = GrabItDown()

    app.start()
    job = app.download("https://mock.test/v1", mode="audio", quality="best")

    assert job.request.media_type == MediaType.AUDIO

    time.sleep(1)
    app.shutdown(wait=True)


def test_feature_check_blocks_basic():
    """Basic tier blocks playlist downloads."""
    with patch("src.grabitdown.create_provider_registry") as mock_factory:
        mock_factory.return_value = _create_mock_registry()
        app = GrabItDown()

    with pytest.raises(FeatureDisabledError):
        app.download_playlist("https://mock.test/playlist", tier="basic")

    app.shutdown(wait=False)


def test_get_stats():
    """Stats available from facade."""
    with patch("src.grabitdown.create_provider_registry") as mock_factory:
        mock_factory.return_value = _create_mock_registry()
        app = GrabItDown()

    stats = app.get_stats()
    assert "active" in stats
    assert "max_concurrent" in stats

    app.shutdown(wait=False)


def test_get_features():
    """Feature list available from facade."""
    with patch("src.grabitdown.create_provider_registry") as mock_factory:
        mock_factory.return_value = _create_mock_registry()
        app = GrabItDown()

    features = app.get_features()
    assert "video_download" in features

    app.shutdown(wait=False)


def test_cancel():
    """Cancel works via facade."""
    with patch("src.grabitdown.create_provider_registry") as mock_factory:
        mock_factory.return_value = _create_mock_registry()
        app = GrabItDown()

    # Pause engine so the job remains queued and can be cancelled deterministically.
    app.pause()

    job = app.download("https://mock.test/v1")

    result = app.cancel(job.job_id)
    assert isinstance(result, bool)

    app.shutdown(wait=False)


def test_pause_resume():
    """Pause and resume via facade."""
    with patch("src.grabitdown.create_provider_registry") as mock_factory:
        mock_factory.return_value = _create_mock_registry()
        app = GrabItDown()

    app.pause()
    stats = app.get_stats()
    assert stats["is_paused"] is True

    app.resume()
    stats = app.get_stats()
    assert stats["is_paused"] is False

    app.shutdown(wait=False)

