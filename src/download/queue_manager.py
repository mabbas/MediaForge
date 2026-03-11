"""GrabItDown download queue — manages pending download jobs with priority support."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Deque, Dict, List

from src.exceptions import LimitExceededError
from src.models.download import DownloadJob

logger = logging.getLogger(__name__)


class DownloadQueue:
    """Thread-safe download queue with priority support."""

    def __init__(self, max_size: int = 100) -> None:
        """Initialize queue with maximum size."""
        self._max_size = max_size
        self._queues: Dict[str, Deque[DownloadJob]] = {
            "high": deque(),
            "normal": deque(),
            "low": deque(),
        }
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)

    def put(self, job: DownloadJob) -> None:
        """Add a job to the queue."""
        with self._lock:
            total = self.size
            if self._max_size != -1 and total >= self._max_size:
                raise LimitExceededError(
                    feature_name="download_queue",
                    current_usage=total,
                    max_allowed=self._max_size,
                )
            priority = job.priority
            if priority not in self._queues:
                priority = "normal"
            self._queues[priority].append(job)
            self._not_empty.notify()
            logger.debug(
                "Job %s queued (priority=%s, queue_size=%s)",
                job.job_id[:8],
                priority,
                self.size,
            )

    def get(self, timeout: float | None = None) -> DownloadJob | None:
        """Get the next job from the queue."""
        with self._not_empty:
            while self.size == 0:
                if not self._not_empty.wait(timeout=timeout):
                    return None

            for priority in ["high", "normal", "low"]:
                queue = self._queues[priority]
                if queue:
                    job = queue.popleft()
                    logger.debug("Job %s dequeued (priority=%s)", job.job_id[:8], priority)
                    return job
            return None

    def peek(self) -> DownloadJob | None:
        """Peek at the next job without removing it."""
        with self._lock:
            for priority in ["high", "normal", "low"]:
                queue = self._queues[priority]
                if queue:
                    return queue[0]
            return None

    def remove(self, job_id: str) -> bool:
        """Remove a specific job from the queue."""
        with self._lock:
            for priority, queue in self._queues.items():
                for idx, job in enumerate(queue):
                    if job.job_id == job_id:
                        del queue[idx]
                        logger.debug("Job %s removed from queue (priority=%s)", job_id[:8], priority)
                        return True
            return False

    def reorder(self, job_ids: List[str]) -> None:
        """Reorder jobs in the normal priority queue."""
        with self._lock:
            queue = self._queues["normal"]
            jobs_by_id = {j.job_id: j for j in queue}

            ordered: List[DownloadJob] = []
            remaining: List[DownloadJob] = []

            for jid in job_ids:
                if jid in jobs_by_id:
                    ordered.append(jobs_by_id[jid])

            for job in queue:
                if job.job_id not in job_ids:
                    remaining.append(job)

            self._queues["normal"] = deque(ordered + remaining)

    def change_priority(self, job_id: str, new_priority: str) -> bool:
        """Change a job's priority."""
        with self._lock:
            for priority, queue in self._queues.items():
                for idx, job in enumerate(queue):
                    if job.job_id == job_id:
                        del queue[idx]
                        job.priority = new_priority
                        target = new_priority if new_priority in self._queues else "normal"
                        self._queues[target].append(job)
                        return True
            return False

    def move_up(self, job_id: str) -> bool:
        """Move a job one position up within its priority queue. Returns True if moved."""
        with self._lock:
            for _priority, queue in self._queues.items():
                for idx, job in enumerate(queue):
                    if job.job_id == job_id:
                        if idx <= 0:
                            return False
                        # Swap with previous
                        queue[idx], queue[idx - 1] = queue[idx - 1], queue[idx]
                        logger.debug("Job %s moved up (priority=%s)", job_id[:8], _priority)
                        return True
            return False

    def move_down(self, job_id: str) -> bool:
        """Move a job one position down within its priority queue. Returns True if moved."""
        with self._lock:
            for _priority, queue in self._queues.items():
                for idx, job in enumerate(queue):
                    if job.job_id == job_id:
                        if idx >= len(queue) - 1:
                            return False
                        # Swap with next
                        queue[idx], queue[idx + 1] = queue[idx + 1], queue[idx]
                        logger.debug("Job %s moved down (priority=%s)", job_id[:8], _priority)
                        return True
            return False

    def get_all_jobs(self) -> List[DownloadJob]:
        """Get all queued jobs in priority order."""
        with self._lock:
            result: List[DownloadJob] = []
            for priority in ["high", "normal", "low"]:
                result.extend(self._queues[priority])
            return result

    @property
    def size(self) -> int:
        """Total number of jobs across all queues."""
        return sum(len(q) for q in self._queues.values())

    @property
    def is_empty(self) -> bool:
        """Return True if the queue is empty."""
        return self.size == 0

    def clear(self) -> None:
        """Remove all jobs from the queue."""
        with self._lock:
            for queue in self._queues.values():
                queue.clear()

    def get_stats(self) -> Dict[str, int]:
        """Queue statistics."""
        with self._lock:
            return {
                "total": self.size,
                "high": len(self._queues["high"]),
                "normal": len(self._queues["normal"]),
                "low": len(self._queues["low"]),
                "max_size": self._max_size,
            }

