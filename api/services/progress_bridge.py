"""Progress bridge — forwards engine progress to WebSocket/broadcast.

Runs in a background thread with its own asyncio loop (same pattern
as EngineDatabaseSync). Listens to engine progress_tracker and can
push updates to WebSocket clients.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from src.models.download import DownloadProgress

logger = logging.getLogger(__name__)


class ProgressBridge:
    """Bridges engine progress to async consumers (e.g. WebSocket)."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="gid-progress-bridge",
            daemon=True,
        )
        self._thread.start()
        logger.info("Progress bridge started")

    def stop(self) -> None:
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Progress bridge stopped")

    def on_progress(self, job_id: str, progress: DownloadProgress) -> None:
        """Called from download engine worker threads."""
        if not self._running or not self._loop:
            return
        # Can push to WebSocket queue here; for now no-op
        asyncio.run_coroutine_threadsafe(
            self._broadcast(job_id, progress),
            self._loop,
        )

    async def _broadcast(self, job_id: str, progress: DownloadProgress) -> None:
        """Broadcast progress (e.g. to WebSocket). No-op for now."""
        pass

    def _run_loop(self) -> None:
        if self._loop:
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()


_bridge: ProgressBridge | None = None


def get_progress_bridge() -> ProgressBridge:
    global _bridge
    if _bridge is None:
        _bridge = ProgressBridge()
    return _bridge


def reset_progress_bridge() -> None:
    global _bridge
    if _bridge:
        _bridge.stop()
    _bridge = None
