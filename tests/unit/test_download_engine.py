"""Tests for GrabItDown download engine."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from src.config import get_settings
from src.core.base_provider import BaseMediaProvider
from src.core.interfaces import ProviderCapabilities, ProgressCallback
from src.core.provider_registry import ProviderRegistry
from src.download.download_engine import DownloadEngine
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
    """Mock provider for testing DownloadEngine."""

    def __init__(self, delay: float = 0.1, fail: bool = False) -> None:
        self._delay = delay
        self._fail = fail

    @property
    def name(self) -> str:
        """Return provider name."""
        return "MockDownloadProvider"

    @property
    def provider_type(self) -> ProviderType:
        """Return provider type."""
        return ProviderType.GENERIC

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return provider capabilities."""
        return ProviderCapabilities(supported_domains=["mock.test"])

    def can_handle(self, url: str) -> bool:
        """Handle URLs under mock.test."""
        return url.startswith("https://mock.test/")

    def extract_info(self, url: str) -> Any:
        """Not used in engine tests."""
        return None

    def get_formats(self, url: str) -> list[Any]:
        """Not used in engine tests."""
        return []

    def download(
        self,
        request: DownloadRequest,
        output_dir: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Simulate a download with optional failure."""
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


def _make_request(url: str) -> DownloadRequest:
    return DownloadRequest(
        url=url,
        media_type=MediaType.VIDEO,
        quality=Quality.Q_1080P,
        video_format=VideoFormat.MP4,
        audio_format=AudioFormat.MP3,
    )


def test_engine_initialization() -> None:
    """Engine initializes with correct settings."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=3, max_queue_size=50)
    stats = engine.get_stats()
    assert stats["max_concurrent"] == 3
    assert stats["active"] == 0
    assert stats["is_paused"] is False
    engine.shutdown(wait=False)


def test_submit_single_download() -> None:
    """Submit creates job and returns immediately."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=10)

    job = engine.submit_download(_make_request("https://mock.test/v1"))

    assert job.job_id is not None
    assert len(job.job_id) == 36
    assert job.progress.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING)

    time.sleep(1)

    updated_job = engine.get_job(job.job_id)
    assert updated_job is not None

    engine.shutdown(wait=True)


def test_submit_batch() -> None:
    """Submit batch creates multiple jobs."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=3, max_queue_size=10)

    requests = [_make_request(f"https://mock.test/v{i}") for i in range(5)]
    jobs = engine.submit_batch(requests)

    assert len(jobs) == 5
    for job in jobs:
        assert job.job_id is not None

    engine.shutdown(wait=True)


def test_concurrent_downloads() -> None:
    """Multiple downloads run concurrently."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider(delay=0.5))
    engine = DownloadEngine(registry, max_concurrent=3, max_queue_size=10)

    requests = [_make_request(f"https://mock.test/v{i}") for i in range(3)]
    engine.submit_batch(requests)

    # Wait long enough that, with proper concurrency, all three should finish.
    time.sleep(3.0)

    stats = engine.get_stats()
    # All jobs should be completed and none queued.
    assert stats["queue"]["total"] == 0
    assert stats["jobs_by_status"].get("completed", 0) == 3

    engine.shutdown(wait=True)


def test_concurrency_limit_respected() -> None:
    """Never exceeds max_concurrent limit."""
    registry = ProviderRegistry()
    max_active_seen = 0
    active_count = [0]
    lock = threading.Lock()

    class CountingProvider(MockDownloadProvider):
        def download(
            self,
            request: DownloadRequest,
            output_dir: str | None = None,
            progress_callback: ProgressCallback | None = None,
        ) -> DownloadResult:
            nonlocal max_active_seen
            with lock:
                active_count[0] += 1
                if active_count[0] > max_active_seen:
                    max_active_seen = active_count[0]
            time.sleep(0.3)
            with lock:
                active_count[0] -= 1
            return super().download(request, output_dir, progress_callback)

    registry.register(CountingProvider())
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=20)

    for i in range(6):
        engine.submit_download(_make_request(f"https://mock.test/v{i}"))

    time.sleep(3)

    assert max_active_seen <= 2, f"Max active was {max_active_seen}, expected <= 2"

    engine.shutdown(wait=True)


def test_cancel_queued_job() -> None:
    """Cancel removes job from queue."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider(delay=0.1))
    engine = DownloadEngine(registry, max_concurrent=1, max_queue_size=10)

    # Pause engine so jobs stay queued and are not picked up by workers.
    engine.pause_all()

    job1 = engine.submit_download(_make_request("https://mock.test/v1"))
    job2 = engine.submit_download(_make_request("https://mock.test/v2"))

    result = engine.cancel_job(job2.job_id)
    assert result is True

    engine.shutdown(wait=False)


def test_cancel_all() -> None:
    """Cancel all clears queue and active jobs."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider(delay=0.1))
    engine = DownloadEngine(registry, max_concurrent=1, max_queue_size=10)

    # Pause engine so all jobs remain queued (no long-running workers).
    engine.pause_all()

    submitted = 5
    for i in range(submitted):
        engine.submit_download(_make_request(f"https://mock.test/v{i}"))

    count = engine.cancel_all()
    assert count == submitted

    engine.shutdown(wait=False)


def test_pause_and_resume() -> None:
    """Pausing stops new job pickup."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider(delay=0.1))
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=10)

    engine.pause_all()
    assert engine.is_paused is True

    job = engine.submit_download(_make_request("https://mock.test/v1"))
    time.sleep(0.5)

    assert engine.active_count == 0

    engine.resume_all()
    assert engine.is_paused is False
    time.sleep(1)

    engine.shutdown(wait=True)


def test_get_stats() -> None:
    """Stats reflect engine state."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=3, max_queue_size=100)

    stats = engine.get_stats()
    assert "active" in stats
    assert "max_concurrent" in stats
    assert "queue" in stats
    assert "jobs_by_status" in stats
    assert "is_paused" in stats
    assert stats["max_concurrent"] == 3

    engine.shutdown(wait=False)


def test_progress_tracking() -> None:
    """Progress updates are tracked per job."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider(delay=0.1))
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=10)

    progress_received: list[tuple[str, DownloadStatus]] = []

    engine.progress_tracker.add_listener(
        lambda jid, p: progress_received.append((jid, p.status))
    )

    job = engine.submit_download(_make_request("https://mock.test/v1"))

    time.sleep(1)

    assert len(progress_received) > 0

    engine.shutdown(wait=True)


def test_set_max_concurrent() -> None:
    """Can change max concurrent at runtime."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=10)

    engine.set_max_concurrent(4)
    stats = engine.get_stats()
    assert stats["max_concurrent"] == 4

    engine.shutdown(wait=False)


def test_set_max_concurrent_capped() -> None:
    """Max concurrent capped at absolute max."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=10)

    engine.set_max_concurrent(100)
    stats = engine.get_stats()
    assert stats["max_concurrent"] <= get_settings().download.absolute_max_concurrent

    engine.shutdown(wait=False)


def test_user_and_tenant_tracking() -> None:
    """Jobs track user and tenant IDs."""
    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=10)

    job = engine.submit_download(
        _make_request("https://mock.test/v1"),
        user_id="alice",
        tenant_id="acme-corp",
    )

    assert job.user_id == "alice"
    assert job.tenant_id == "acme-corp"

    engine.shutdown(wait=False)


def test_invalid_url_provider_error() -> None:
    """Submit with URL no provider handles raises ProviderError."""
    from src.exceptions import ProviderError

    registry = ProviderRegistry()
    registry.register(MockDownloadProvider())
    engine = DownloadEngine(registry, max_concurrent=2, max_queue_size=10)

    with pytest.raises(ProviderError):
        engine.submit_download(_make_request("https://unknown.com/video"))

    engine.shutdown(wait=False)

