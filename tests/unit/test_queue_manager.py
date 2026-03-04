"""Tests for GrabItDown download queue."""

import pytest

from src.download.queue_manager import DownloadQueue
from src.exceptions import LimitExceededError
from src.models.download import DownloadJob, DownloadProgress, DownloadRequest
from src.models.enums import DownloadStatus, MediaType, Quality, VideoFormat, AudioFormat


def _make_request(url: str = "https://test.com") -> DownloadRequest:
    return DownloadRequest(
        url=url,
        media_type=MediaType.VIDEO,
        quality=Quality.Q_1080P,
        video_format=VideoFormat.MP4,
        audio_format=AudioFormat.MP3,
    )


def test_put_and_get() -> None:
    """Basic enqueue and dequeue."""
    queue = DownloadQueue(max_size=10)
    job = DownloadJob(
        request=_make_request("https://test.com"),
        progress=DownloadProgress(
            job_id="test",
            status=DownloadStatus.QUEUED,
        ),
    )
    queue.put(job)
    assert queue.size == 1
    result = queue.get(timeout=1)
    assert result is not None
    assert result.job_id == job.job_id


def test_priority_ordering() -> None:
    """High priority jobs dequeued first."""
    queue = DownloadQueue(max_size=10)

    normal_job = DownloadJob(
        request=_make_request("https://normal.com"),
        progress=DownloadProgress(
            job_id="normal",
            status=DownloadStatus.QUEUED,
        ),
        priority="normal",
    )
    high_job = DownloadJob(
        request=_make_request("https://high.com"),
        progress=DownloadProgress(
            job_id="high",
            status=DownloadStatus.QUEUED,
        ),
        priority="high",
    )

    queue.put(normal_job)
    queue.put(high_job)

    first = queue.get(timeout=1)
    assert first is not None
    assert first.priority == "high"
    second = queue.get(timeout=1)
    assert second is not None
    assert second.priority == "normal"


def test_fifo_within_priority() -> None:
    """Jobs of same priority are FIFO."""
    queue = DownloadQueue(max_size=10)
    for i in range(3):
        job = DownloadJob(
            job_id=f"job-{i}",
            request=_make_request(f"https://test{i}.com"),
            progress=DownloadProgress(
                job_id=f"job-{i}",
                status=DownloadStatus.QUEUED,
            ),
        )
        queue.put(job)

    for i in range(3):
        result = queue.get(timeout=1)
        assert result is not None
        assert result.job_id == f"job-{i}"


def test_queue_full() -> None:
    """Exceeding max_size raises LimitExceededError."""
    queue = DownloadQueue(max_size=2)
    for i in range(2):
        queue.put(
            DownloadJob(
                request=_make_request(f"https://t{i}.com"),
                progress=DownloadProgress(
                    job_id=f"j{i}",
                    status=DownloadStatus.QUEUED,
                ),
            )
        )

    with pytest.raises(LimitExceededError):
        queue.put(
            DownloadJob(
                request=_make_request("https://overflow.com"),
                progress=DownloadProgress(
                    job_id="overflow",
                    status=DownloadStatus.QUEUED,
                ),
            )
        )


def test_queue_unlimited() -> None:
    """Queue with max_size=-1 has no limit."""
    queue = DownloadQueue(max_size=-1)
    for i in range(50):
        queue.put(
            DownloadJob(
                request=_make_request(f"https://t{i}.com"),
                progress=DownloadProgress(
                    job_id=f"j{i}",
                    status=DownloadStatus.QUEUED,
                ),
            )
        )
    assert queue.size == 50


def test_remove_job() -> None:
    """Remove specific job from queue."""
    queue = DownloadQueue(max_size=10)
    job = DownloadJob(
        request=_make_request("https://test.com"),
        progress=DownloadProgress(
            job_id="test",
            status=DownloadStatus.QUEUED,
        ),
    )
    queue.put(job)
    assert queue.remove(job.job_id) is True
    assert queue.size == 0


def test_remove_nonexistent() -> None:
    """Removing nonexistent job returns False."""
    queue = DownloadQueue(max_size=10)
    assert queue.remove("nonexistent") is False


def test_peek() -> None:
    """Peek returns next job without removing."""
    queue = DownloadQueue(max_size=10)
    job = DownloadJob(
        request=_make_request("https://test.com"),
        progress=DownloadProgress(
            job_id="test",
            status=DownloadStatus.QUEUED,
        ),
    )
    queue.put(job)
    peeked = queue.peek()
    assert peeked is not None
    assert queue.size == 1


def test_change_priority() -> None:
    """Change job priority within queue."""
    queue = DownloadQueue(max_size=10)
    job = DownloadJob(
        request=_make_request("https://test.com"),
        progress=DownloadProgress(
            job_id="test",
            status=DownloadStatus.QUEUED,
        ),
        priority="low",
    )
    queue.put(job)
    assert queue.change_priority(job.job_id, "high") is True
    result = queue.get(timeout=1)
    assert result is not None
    assert result.priority == "high"


def test_get_all_jobs() -> None:
    """get_all_jobs returns jobs in priority order."""
    queue = DownloadQueue(max_size=10)
    for p in ["low", "high", "normal"]:
        queue.put(
            DownloadJob(
                request=_make_request(f"https://{p}.com"),
                progress=DownloadProgress(
                    job_id=p,
                    status=DownloadStatus.QUEUED,
                ),
                priority=p,
            )
        )

    all_jobs = queue.get_all_jobs()
    assert len(all_jobs) == 3
    assert all_jobs[0].priority == "high"
    assert all_jobs[1].priority == "normal"
    assert all_jobs[2].priority == "low"


def test_get_stats() -> None:
    """Stats reflect queue state."""
    queue = DownloadQueue(max_size=100)
    queue.put(
        DownloadJob(
            request=_make_request("https://test.com"),
            progress=DownloadProgress(
                job_id="test",
                status=DownloadStatus.QUEUED,
            ),
            priority="high",
        )
    )
    stats = queue.get_stats()
    assert stats["total"] == 1
    assert stats["high"] == 1
    assert stats["normal"] == 0
    assert stats["max_size"] == 100


def test_clear() -> None:
    """Clear empties the queue."""
    queue = DownloadQueue(max_size=10)
    for i in range(5):
        queue.put(
            DownloadJob(
                request=_make_request(f"https://t{i}.com"),
                progress=DownloadProgress(
                    job_id=f"j{i}",
                    status=DownloadStatus.QUEUED,
                ),
            )
        )
    queue.clear()
    assert queue.size == 0
    assert queue.is_empty is True


def test_get_timeout() -> None:
    """Get with timeout returns None on empty queue."""
    queue = DownloadQueue(max_size=10)
    result = queue.get(timeout=0.1)
    assert result is None

