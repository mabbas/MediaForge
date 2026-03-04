"""GrabItDown engine-to-database synchronization.

Listens to download engine progress and automatically
updates the database when job status changes.
This is the glue between the in-memory engine and
persistent storage.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import Future

from src.models.download import DownloadProgress
from src.models.enums import DownloadStatus

logger = logging.getLogger(__name__)

TERMINAL_STATES = {
    DownloadStatus.COMPLETED,
    DownloadStatus.FAILED,
    DownloadStatus.CANCELLED,
}


class EngineDatabaseSync:
    """Synchronizes engine events to database.

    When main_loop is set, DB updates run on that loop (required: async
    session/engine are bound to the main app loop). Otherwise uses a
    background thread with its own loop (may hit "different loop" with PostgreSQL).
    """

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._throttle_ms = 2000  # DB updates every 2s per job
        self._last_update: dict[str, float] = {}
        self._pending: list[Future[None]] = []
        self._pending_lock = threading.Lock()

    def set_main_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the main app event loop so DB updates run there (avoids 'different loop' errors)."""
        self._main_loop = loop

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        with self._pending_lock:
            self._pending = []
        if self._main_loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="gid-engine-db-sync",
                daemon=True,
            )
            self._thread.start()
        logger.info("Engine->DB sync started")

    def stop(self) -> None:
        self._running = False
        # Wait briefly for in-flight syncs so no coroutine is left unawaited
        with self._pending_lock:
            pending = list(self._pending)
        for f in pending:
            try:
                f.result(timeout=2)
            except Exception:
                pass
        with self._pending_lock:
            self._pending.clear()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Engine->DB sync stopped")

    def on_progress(
        self,
        job_id: str,
        progress: DownloadProgress,
    ) -> None:
        """Called from download engine worker threads."""
        target_loop = self._main_loop if self._main_loop is not None else self._loop
        if not self._running or target_loop is None:
            return

        now = time.time() * 1000
        last = self._last_update.get(job_id, 0)

        # Always sync terminal states immediately
        is_terminal = progress.status in TERMINAL_STATES

        if not is_terminal and (now - last) < self._throttle_ms:
            return

        self._last_update[job_id] = now

        future: Future[None] = asyncio.run_coroutine_threadsafe(
            self._sync_to_db(job_id, progress),
            target_loop,  # main app loop so asyncpg session runs in correct loop
        )
        with self._pending_lock:
            self._pending.append(future)

        def _remove(_: object) -> None:
            with self._pending_lock:
                if future in self._pending:
                    self._pending.remove(future)

        future.add_done_callback(_remove)

    async def _sync_to_db(
        self,
        job_id: str,
        progress: DownloadProgress,
    ) -> None:
        """Update job in database."""
        try:
            from api.database.connection import async_session_factory

            if async_session_factory is None:
                return

            async with async_session_factory() as session:
                from api.services.download_service import update_job_status

                await update_job_status(
                    session,
                    job_id=job_id,
                    status=progress.status.value,
                    progress_percent=progress.percent,
                    speed_bps=progress.speed_bytes_per_second,
                    eta_seconds=progress.eta_seconds,
                    bytes_downloaded=progress.bytes_downloaded,
                    total_bytes=progress.total_bytes,
                )
                await session.commit()

        except Exception as e:
            logger.warning("DB sync failed for %s: %s", job_id[:8], e)

    def _run_loop(self) -> None:
        if self._loop:
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()


# Singleton
_sync: EngineDatabaseSync | None = None


def get_engine_sync() -> EngineDatabaseSync:
    global _sync
    if _sync is None:
        _sync = EngineDatabaseSync()
    return _sync


def reset_engine_sync() -> None:
    global _sync
    if _sync:
        _sync.stop()
    _sync = None
