"""GrabItDown progress tracking — manages progress state for all active downloads."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List

from src.models.download import DownloadProgress

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Thread-safe progress tracker for all active downloads.

    Stores current progress for each job_id and notifies registered listeners on updates.
    """

    def __init__(self) -> None:
        self._progress: Dict[str, DownloadProgress] = {}
        self._listeners: List[Callable[[str, DownloadProgress], None]] = []
        self._lock = threading.Lock()

    def update(self, job_id: str, progress: DownloadProgress) -> None:
        """Update progress for a job and notify listeners."""
        with self._lock:
            self._progress[job_id] = progress

        for listener in self._listeners:
            try:
                listener(job_id, progress)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Progress listener error: %s", exc)

    def get(self, job_id: str) -> DownloadProgress | None:
        """Get current progress for a job."""
        with self._lock:
            return self._progress.get(job_id)

    def get_all(self) -> Dict[str, DownloadProgress]:
        """Get progress for all active jobs."""
        with self._lock:
            return dict(self._progress)

    def remove(self, job_id: str) -> None:
        """Remove progress tracking for a completed/cancelled job."""
        with self._lock:
            self._progress.pop(job_id, None)

    def add_listener(self, listener: Callable[[str, DownloadProgress], None]) -> None:
        """Register a progress listener."""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str, DownloadProgress], None]) -> None:
        """Unregister a progress listener."""
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    @property
    def active_count(self) -> int:
        """Number of actively tracked jobs."""
        with self._lock:
            return len(self._progress)

    def clear(self) -> None:
        """Clear all progress tracking."""
        with self._lock:
            self._progress.clear()

