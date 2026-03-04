"""Tests for GrabItDown progress tracker."""

import threading

from src.download.progress_tracker import ProgressTracker
from src.models.download import DownloadProgress
from src.models.enums import DownloadStatus


def test_update_and_get() -> None:
    """Update progress and retrieve it."""
    tracker = ProgressTracker()
    progress = DownloadProgress(
        job_id="job1",
        status=DownloadStatus.DOWNLOADING,
        percent=50.0,
    )
    tracker.update("job1", progress)
    result = tracker.get("job1")
    assert result is not None
    assert result.percent == 50.0


def test_get_nonexistent() -> None:
    """Get returns None for unknown job_id."""
    tracker = ProgressTracker()
    assert tracker.get("nonexistent") is None


def test_get_all() -> None:
    """get_all returns all tracked progress."""
    tracker = ProgressTracker()
    for i in range(3):
        tracker.update(
            f"job{i}",
            DownloadProgress(
                job_id=f"job{i}",
                status=DownloadStatus.DOWNLOADING,
            ),
        )
    assert len(tracker.get_all()) == 3


def test_remove() -> None:
    """Remove stops tracking a job."""
    tracker = ProgressTracker()
    tracker.update(
        "job1",
        DownloadProgress(
            job_id="job1",
            status=DownloadStatus.DOWNLOADING,
        ),
    )
    tracker.remove("job1")
    assert tracker.get("job1") is None


def test_active_count() -> None:
    """active_count reflects tracked jobs."""
    tracker = ProgressTracker()
    assert tracker.active_count == 0
    tracker.update(
        "job1",
        DownloadProgress(
            job_id="job1",
            status=DownloadStatus.DOWNLOADING,
        ),
    )
    assert tracker.active_count == 1


def test_listener_notification() -> None:
    """Listeners are called on update."""
    tracker = ProgressTracker()
    received: list[tuple[str, DownloadProgress]] = []
    tracker.add_listener(lambda jid, p: received.append((jid, p)))
    tracker.update(
        "job1",
        DownloadProgress(
            job_id="job1",
            status=DownloadStatus.DOWNLOADING,
        ),
    )
    assert len(received) == 1
    assert received[0][0] == "job1"


def test_remove_listener() -> None:
    """Removed listeners stop receiving updates."""
    tracker = ProgressTracker()
    received: list[str] = []

    def listener(jid: str, _: DownloadProgress) -> None:
        received.append(jid)

    tracker.add_listener(listener)
    tracker.update(
        "job1",
        DownloadProgress(
            job_id="job1",
            status=DownloadStatus.DOWNLOADING,
        ),
    )
    tracker.remove_listener(listener)
    tracker.update(
        "job2",
        DownloadProgress(
            job_id="job2",
            status=DownloadStatus.DOWNLOADING,
        ),
    )
    assert len(received) == 1
    assert received[0] == "job1"


def test_thread_safety() -> None:
    """Concurrent updates don't corrupt state."""
    tracker = ProgressTracker()

    def updater(thread_id: int) -> None:
        for i in range(100):
            tracker.update(
                f"t{thread_id}-{i}",
                DownloadProgress(
                    job_id=f"t{thread_id}-{i}",
                    status=DownloadStatus.DOWNLOADING,
                ),
            )

    threads = [threading.Thread(target=updater, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tracker.active_count == 500


def test_clear() -> None:
    """Clear removes all tracking."""
    tracker = ProgressTracker()
    tracker.update(
        "job1",
        DownloadProgress(
            job_id="job1",
            status=DownloadStatus.DOWNLOADING,
        ),
    )
    tracker.clear()
    assert tracker.active_count == 0

