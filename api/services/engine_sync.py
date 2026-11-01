"""GrabItDown engine-to-database synchronization.

Listens to download engine progress and automatically
updates the database when job status changes.
This is the glue between the in-memory engine and
persistent storage.
"""

from __future__ import annotations

from typing import Any

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
        self._engine: Any = None

    def set_engine(self, engine: Any) -> None:
        """Inject engine instance to eliminate circular imports with dependencies.py."""
        self._engine = engine

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

    def _job_result_fields(self, job_id: str) -> dict:
        """Read final result metadata from the in-memory engine (terminal states)."""
        try:
            engine = self._engine
            if engine is None:
                try:
                    from api.dependencies import get_app
                    engine = get_app()._engine
                except Exception:
                    return {}
            job = engine.get_job(job_id)
            if not job or not job.result:
                return {}
            result = job.result
            fields: dict = {}
            if result.file_path:
                fields["file_path"] = result.file_path
            if result.file_size_bytes is not None:
                fields["file_size_bytes"] = result.file_size_bytes
            if result.title:
                fields["title"] = result.title
            if result.error_message:
                fields["error_message"] = result.error_message
            return fields
        except Exception:
            return {}

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

            result_fields: dict = {}
            if progress.status in TERMINAL_STATES:
                result_fields = self._job_result_fields(job_id)

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
                    **result_fields,
                )
                # Atomic commercial download usage recording upon completion (STEP 02-E2B-Fix-A)
                if progress.status == DownloadStatus.COMPLETED:
                    try:
                        from api.database.models import DownloadJobDB, CommercialReservationDB
                        from api.services.usage_service import record_usage_event
                        from api.security.media_storage import get_tenant_storage_root

                        job_record = await session.get(DownloadJobDB, job_id)
                        if job_record and job_record.tenant_id:
                            fsize = result_fields.get("file_size_bytes") or progress.total_bytes or progress.bytes_downloaded or 0
                            # Cross-verify actual completed bytes directly from disk if file exists
                            if job_record.file_path:
                                try:
                                    disk_file = get_tenant_storage_root(job_record.tenant_id) / job_record.file_path
                                    if disk_file.is_file():
                                        fsize = disk_file.stat().st_size
                                except Exception:
                                    pass

                            await record_usage_event(
                                session,
                                tenant_id=job_record.tenant_id,
                                event_type="download",
                                idempotency_key=f"download:{job_id}",
                                resource_id=job_id,
                                quantity=1.0,
                                unit="count",
                                bytes=int(fsize),
                                duration_seconds=float(job_record.duration_seconds or 0.0),
                                user_id=job_record.user_id,
                                auth_method="session",
                                metadata_json={
                                    "url": job_record.url,
                                    "format": job_record.format,
                                    "quality": job_record.quality,
                                },
                            )

                            # Finalize commercial reservation (STEP 04-A2)
                            from sqlalchemy import select
                            stmt_res = select(CommercialReservationDB).where(
                                CommercialReservationDB.resource_id == job_id,
                                CommercialReservationDB.status.in_(["reserved", "active"]),
                            ).with_for_update()
                            res_obj = (await session.execute(stmt_res)).scalars().first()
                            if res_obj:
                                now_dt = datetime.utcnow()
                                res_obj.status = "finalized"
                                res_obj.finalized_at = now_dt
                                res_obj.updated_at = now_dt
                    except Exception as u_exc:
                        logger.warning("Could not record in-transaction download usage for %s: %s", job_id, u_exc)

                elif progress.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                    try:
                        from api.database.models import CommercialReservationDB
                        from sqlalchemy import select
                        stmt_res = select(CommercialReservationDB).where(
                            CommercialReservationDB.resource_id == job_id,
                            CommercialReservationDB.status.in_(["reserved", "active"]),
                        ).with_for_update()
                        res_obj = (await session.execute(stmt_res)).scalars().first()
                        if res_obj:
                            now_dt = datetime.utcnow()
                            res_obj.status = "released"
                            res_obj.released_at = now_dt
                            res_obj.updated_at = now_dt
                    except Exception as r_exc:
                        logger.warning("Could not release commercial reservation for download %s: %s", job_id, r_exc)

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
