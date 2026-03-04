"""Tests for engine-to-database sync."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.models.download import DownloadProgress
from src.models.enums import DownloadStatus


def test_engine_sync_init():
    """EngineDatabaseSync initializes."""
    from api.services.engine_sync import EngineDatabaseSync

    sync = EngineDatabaseSync()
    assert sync._running is False


def test_engine_sync_start_stop():
    """Start and stop cleanly."""
    from api.services.engine_sync import EngineDatabaseSync

    sync = EngineDatabaseSync()
    sync.start()
    assert sync._running is True
    sync.stop()
    assert sync._running is False


def test_engine_sync_throttle():
    """Non-terminal updates throttled."""
    from api.services.engine_sync import EngineDatabaseSync

    sync = EngineDatabaseSync()
    # Don't start loop — just test throttle logic
    sync._running = True
    sync._loop = MagicMock()

    progress = DownloadProgress(
        job_id="test",
        status=DownloadStatus.DOWNLOADING,
        percent=50.0,
    )

    # First call should pass (no exception)
    sync._last_update.clear()
    sync.on_progress("test", progress)
