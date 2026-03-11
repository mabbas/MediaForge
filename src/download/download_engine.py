"""GrabItDown Download Engine — the central orchestrator for all downloads."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List
from uuid import uuid4

from src.config import get_settings
from src.core.provider_registry import ProviderRegistry
from src.exceptions import DownloadCancelledError, DownloadError, LimitExceededError, ProviderError
from src.models.download import DownloadJob, DownloadProgress, DownloadRequest, DownloadResult
from src.models.enums import DownloadStatus, MediaType, ProviderType
from src.models.playlist import PlaylistDownloadRequest
from src.download.progress_tracker import ProgressTracker
from src.download.queue_manager import DownloadQueue
from src.log_safe import safe_str

logger = logging.getLogger(__name__)


def _sanitize_playlist_folder_name(title: str) -> str:
    """Make a playlist title safe for use as a folder name."""
    if not title or not title.strip():
        return "Playlist"
    cleaned = re.sub(r'[/\\:*?"<>|]', "_", title)
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", cleaned)
    cleaned = re.sub(r"[_\s]+", " ", cleaned)
    cleaned = cleaned.strip(". ")
    if len(cleaned) > 120:
        cleaned = cleaned[:120].rsplit(" ", 1)[0] if " " in cleaned[:120] else cleaned[:120]
    return cleaned if cleaned else "Playlist"


class DownloadEngine:
    """Central download orchestrator."""

    def __init__(
        self,
        registry: ProviderRegistry,
        max_concurrent: int | None = None,
        max_queue_size: int | None = None,
    ) -> None:
        """Initialize the download engine."""
        settings = get_settings()

        self._registry = registry
        self._max_concurrent = max_concurrent or settings.download.max_concurrent_downloads
        self._max_queue_size = max_queue_size or settings.download.queue_max_size

        self._executor = ThreadPoolExecutor(
            max_workers=self._max_concurrent,
            thread_name_prefix="gid-download",
        )

        self._jobs: Dict[str, DownloadJob] = {}
        self._futures: Dict[str, Future[DownloadResult]] = {}
        self._jobs_lock = threading.Lock()

        self._progress_tracker = ProgressTracker()
        self._queue = DownloadQueue(self._max_queue_size)

        self._active_count = 0
        self._active_lock = threading.Lock()

        self._is_running = True
        self._is_paused = False
        self._cancelled_job_ids: set = set()
        self._cancelled_lock = threading.Lock()
        self._paused_job_ids: set = set()  # job_ids that were paused (so we set PAUSED not CANCELLED)
        self._deferred: Dict[str, DownloadJob] = {}

        self._queue_thread = threading.Thread(
            target=self._queue_consumer,
            name="gid-queue-consumer",
            daemon=True,
        )
        self._queue_thread.start()

        logger.info(
            "GrabItDown Download Engine initialized (max_concurrent=%s, max_queue=%s)",
            self._max_concurrent,
            self._max_queue_size,
        )

    # ── Submission APIs ──────────────────────────────────────────────────────

    def submit_download(
        self,
        request: DownloadRequest,
        priority: str = "normal",
        user_id: str = "system",
        tenant_id: str = "default",
        start: bool = True,
    ) -> DownloadJob:
        """Submit a single download request. If start=False, job is deferred until start_job/start_all_deferred."""
        provider = self._registry.detect_provider(request.url)

        job_id = str(uuid4())
        status = DownloadStatus.DEFERRED if not start else DownloadStatus.QUEUED
        progress = DownloadProgress(job_id=job_id, status=status)
        job = DownloadJob(
            job_id=job_id,
            request=request,
            progress=progress,
            priority=priority,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        with self._jobs_lock:
            self._jobs[job_id] = job

        if start:
            self._queue.put(job)
        else:
            self._deferred[job_id] = job

        self._progress_tracker.update(job_id, progress)

        logger.info(
            "Download submitted: %s -> %s (priority=%s, start=%s)",
            job_id[:8],
            provider.name,
            priority,
            start,
        )
        return job

    def submit_batch(
        self,
        requests: List[DownloadRequest],
        priority: str = "normal",
        user_id: str = "system",
        tenant_id: str = "default",
        start: bool = True,
    ) -> List[DownloadJob]:
        """Submit multiple download requests."""
        jobs: List[DownloadJob] = []
        for request in requests:
            try:
                job = self.submit_download(
                    request=request,
                    priority=priority,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    start=start,
                )
                jobs.append(job)
            except (ProviderError, LimitExceededError) as exc:
                logger.error("Failed to submit %s: %s", request.url[:50], exc)
                failed_job = DownloadJob(
                    request=request,
                    progress=DownloadProgress(
                        job_id=str(uuid4()),
                        status=DownloadStatus.FAILED,
                    ),
                    priority=priority,
                    user_id=user_id,
                    tenant_id=tenant_id,
                )
                failed_job.result = DownloadResult(
                    job_id=failed_job.job_id,
                    url=request.url,
                    provider=ProviderType.GENERIC,
                    status=DownloadStatus.FAILED,
                    title="Unknown",
                    media_type=request.media_type,
                    error_message=str(exc),
                )
                jobs.append(failed_job)

        logger.info("Batch submitted: %s jobs", len(jobs))
        return jobs

    def submit_playlist(
        self,
        request: PlaylistDownloadRequest,
        priority: str = "normal",
        user_id: str = "system",
        tenant_id: str = "default",
        start: bool = True,
    ) -> tuple[str, List[DownloadJob]]:
        """Submit a playlist for download."""
        provider = self._registry.detect_provider(request.url)
        parent_job_id = str(uuid4())

        if provider.provider_type == ProviderType.YOUTUBE:
            from src.providers.youtube.playlist import YouTubePlaylistHandler

            handler = YouTubePlaylistHandler(provider)
            playlist_info = handler.get_playlist_info(request.url)
        else:
            raise ProviderError(
                f"{provider.name} does not support playlists",
                provider=provider.name,
            )

        if request.items == "all":
            items = playlist_info.available_items
        else:
            items = [item for item in playlist_info.available_items if item.index in request.items]

        base_dir = request.output_directory or "."
        playlist_folder = _sanitize_playlist_folder_name(playlist_info.title)
        playlist_output_dir = os.path.join(base_dir, playlist_folder)

        child_jobs: List[DownloadJob] = []
        for item in items:
            dl_request = DownloadRequest(
                url=item.url,
                media_type=request.media_type,
                quality=request.quality,
                video_format=request.video_format,
                audio_format=request.audio_format,
                audio_bitrate=request.audio_bitrate,
                output_directory=playlist_output_dir,
                embed_subtitles=request.embed_subtitles,
                subtitle_languages=request.subtitle_languages,
            )
            job = self.submit_download(
                request=dl_request,
                priority=priority,
                user_id=user_id,
                tenant_id=tenant_id,
                start=start,
            )
            job.parent_job_id = parent_job_id
            job.playlist_index = item.index
            job.title = item.title
            child_jobs.append(job)

        logger.info(
            "Playlist submitted: %s -> %s videos from '%s' -> %s",
            parent_job_id[:8],
            len(child_jobs),
            safe_str(playlist_info.title),
            safe_str(playlist_output_dir),
        )
        return parent_job_id, child_jobs

    # ── Job control ──────────────────────────────────────────────────────────

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a queued, deferred, or active download."""
        was_deferred = False
        with self._jobs_lock:
            if job_id in self._deferred:
                self._deferred.pop(job_id, None)
                was_deferred = True
        if was_deferred:
            self._update_job_status(job_id, DownloadStatus.CANCELLED)
            logger.info("Job %s cancelled (was deferred)", job_id[:8])
            return True

        if self._queue.remove(job_id):
            self._update_job_status(job_id, DownloadStatus.CANCELLED)
            logger.info("Job %s cancelled (was queued)", job_id[:8])
            return True

        with self._jobs_lock:
            future = self._futures.get(job_id)
            if future and not future.done():
                with self._cancelled_lock:
                    self._cancelled_job_ids.add(job_id)
                future.cancel()  # may not stop already-running thread; progress_callback will check _cancelled_job_ids
                logger.info("Job %s cancel requested (active)", job_id[:8])
                return True

        return False

    def start_job(self, job_id: str) -> bool:
        """Move a deferred job to the queue so it will be started. Returns True if job was deferred and is now queued."""
        with self._jobs_lock:
            job = self._deferred.pop(job_id, None)
            if not job:
                return False
            job.progress.status = DownloadStatus.QUEUED
            self._progress_tracker.update(job_id, job.progress)
        self._queue.put(job)
        logger.info("Deferred job %s started (moved to queue)", job_id[:8])
        return True

    def pause_job(self, job_id: str) -> bool:
        """Pause a queued, deferred, or active download (sets status to PAUSED so it can be resumed). Returns True if the job was paused."""
        with self._jobs_lock:
            if job_id in self._deferred:
                self._deferred.pop(job_id, None)
                self._update_job_status(job_id, DownloadStatus.PAUSED)
                logger.info("Job %s paused (was deferred)", job_id[:8])
                return True
        if self._queue.remove(job_id):
            self._update_job_status(job_id, DownloadStatus.PAUSED)
            logger.info("Job %s paused (was queued)", job_id[:8])
            return True
        with self._jobs_lock:
            future = self._futures.get(job_id)
            if future and not future.done():
                with self._cancelled_lock:
                    self._cancelled_job_ids.add(job_id)
                self._paused_job_ids.add(job_id)
                future.cancel()
                logger.info("Job %s pause requested (active)", job_id[:8])
                return True
        return False

    def requeue_job(self, job_id: str) -> bool:
        """Re-queue a failed/paused/interrupted job so it runs again (same job_id). Returns True if the job was requeued."""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if not job or not job.progress:
                return False
            status = job.progress.status
            status_val = status.value if hasattr(status, "value") else str(status)
            if status_val not in ("failed", "paused", "interrupted"):
                return False
            job.progress.status = DownloadStatus.QUEUED
            job.progress.percent = 0.0
            job.progress.bytes_downloaded = 0
            job.progress.total_bytes = None
            job.result = None
            self._progress_tracker.update(job_id, job.progress)
        self._queue.put(job)
        logger.info("Job %s requeued (was %s)", job_id[:8], status_val)
        return True

    def start_all_deferred(self) -> int:
        """Move all deferred jobs to the queue. Returns the number of jobs started."""
        with self._jobs_lock:
            to_start = list(self._deferred.values())
            self._deferred.clear()
        for job in to_start:
            job.progress.status = DownloadStatus.QUEUED
            self._progress_tracker.update(job.job_id, job.progress)
            self._queue.put(job)
        if to_start:
            logger.info("Started %s deferred jobs", len(to_start))
        return len(to_start)

    def cancel_all(self) -> int:
        """Cancel all queued, deferred, and active downloads."""
        count = 0

        with self._jobs_lock:
            deferred_ids = list(self._deferred.keys())
            self._deferred.clear()
        for jid in deferred_ids:
            self._update_job_status(jid, DownloadStatus.CANCELLED)
            count += 1

        for job in self._queue.get_all_jobs():
            if self.cancel_job(job.job_id):
                count += 1

        with self._jobs_lock:
            for job_id, future in list(self._futures.items()):
                if not future.done():
                    with self._cancelled_lock:
                        self._cancelled_job_ids.add(job_id)
                    future.cancel()
                    count += 1

        logger.info("Cancelled %s jobs", count)
        return count

    def pause_all(self) -> None:
        """Pause the download engine (no new jobs consumed)."""
        self._is_paused = True
        logger.info("Download engine paused")

    def resume_all(self) -> None:
        """Resume the download engine."""
        self._is_paused = False
        logger.info("Download engine resumed")

    # ── Status & progress ────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> DownloadJob | None:
        """Get a job by ID."""
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def get_job_progress(self, job_id: str) -> DownloadProgress | None:
        """Get current progress for a job."""
        return self._progress_tracker.get(job_id)

    def get_all_jobs(self) -> List[DownloadJob]:
        """Get all tracked jobs in display order: downloading, queued (queue order), deferred, then rest."""
        with self._jobs_lock:
            all_jobs = list(self._jobs.values())
        queue_ordered = self._queue.get_all_jobs()
        queue_ids = {j.job_id for j in queue_ordered}
        queue_list = list(queue_ordered)
        downloading: List[DownloadJob] = []
        deferred: List[DownloadJob] = []
        rest: List[DownloadJob] = []
        for job in all_jobs:
            sid = job.progress.status
            if sid == DownloadStatus.DOWNLOADING:
                downloading.append(job)
            elif job.job_id in queue_ids:
                pass  # already in queue_list
            elif sid == DownloadStatus.DEFERRED:
                deferred.append(job)
            else:
                rest.append(job)
        return downloading + queue_list + deferred + rest

    def get_active_jobs(self) -> List[DownloadJob]:
        """Get currently downloading jobs."""
        with self._jobs_lock:
            return [job for job in self._jobs.values() if job.progress.status == DownloadStatus.DOWNLOADING]

    def get_stats(self) -> Dict[str, object]:
        """Engine statistics."""
        with self._jobs_lock:
            jobs_by_status: Dict[str, int] = {}
            for job in self._jobs.values():
                status = job.progress.status.value
                jobs_by_status[status] = jobs_by_status.get(status, 0) + 1

        return {
            "active": self._active_count,
            "max_concurrent": self._max_concurrent,
            "queue": self._queue.get_stats(),
            "jobs_by_status": jobs_by_status,
            "total_jobs": len(self._jobs),
            "is_paused": self._is_paused,
            "is_running": self._is_running,
        }

    def move_job_up(self, job_id: str) -> bool:
        """Move a queued job one position up. Returns True if moved."""
        return self._queue.move_up(job_id)

    def move_job_down(self, job_id: str) -> bool:
        """Move a queued job one position down. Returns True if moved."""
        return self._queue.move_down(job_id)

    @property
    def progress_tracker(self) -> ProgressTracker:
        """Access the progress tracker for listener registration."""
        return self._progress_tracker

    @property
    def is_paused(self) -> bool:
        """Return whether the engine is paused."""
        return self._is_paused

    @property
    def active_count(self) -> int:
        """Return the number of currently active downloads."""
        return self._active_count

    # ── Concurrency control ─────────────────────────────────────────────────

    def set_max_concurrent(self, max_concurrent: int) -> None:
        """Change maximum concurrent downloads at runtime."""
        settings = get_settings()
        absolute_max = settings.download.absolute_max_concurrent

        if max_concurrent < 1:
            max_concurrent = 1
        if max_concurrent > absolute_max:
            max_concurrent = absolute_max

        old = self._max_concurrent
        self._max_concurrent = max_concurrent

        # Adjust executor's max workers; relies on internal attribute.
        self._executor._max_workers = max_concurrent  # type: ignore[attr-defined]

        logger.info("Max concurrent changed: %s -> %s", old, max_concurrent)

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the download engine."""
        logger.info("Download engine shutting down (wait=%s)...", wait)
        self._is_running = False

        if not wait:
            self.cancel_all()

        self._executor.shutdown(wait=wait)
        self._progress_tracker.clear()
        logger.info("Download engine shut down")

    # ── Internal helpers ────────────────────────────────────────────────────

    def _queue_consumer(self) -> None:
        """Background thread that consumes jobs from the queue."""
        while self._is_running:
            try:
                if self._is_paused:
                    time.sleep(0.5)
                    continue

                if self._active_count >= self._max_concurrent:
                    time.sleep(0.1)
                    continue

                job = self._queue.get(timeout=0.5)
                if job is None:
                    continue

                with self._active_lock:
                    self._active_count += 1

                future = self._executor.submit(self._execute_download, job)

                with self._jobs_lock:
                    self._futures[job.job_id] = future

                future.add_done_callback(
                    lambda f, jid=job.job_id: self._on_download_complete(jid, f)
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Queue consumer error: %s", exc)
                time.sleep(1)

    def _execute_download(self, job: DownloadJob) -> DownloadResult:
        """Execute a single download in a worker thread."""
        job_id = job.job_id
        request = job.request

        logger.info("Starting download: %s -> %s", job_id[:8], request.url[:60])
        self._update_job_status(job_id, DownloadStatus.DOWNLOADING)

        try:
            provider = self._registry.detect_provider(request.url)

            def progress_callback(progress: DownloadProgress) -> None:
                with self._cancelled_lock:
                    if job_id in self._cancelled_job_ids:
                        raise DownloadCancelledError("Cancelled by user", url=request.url)
                progress.job_id = job_id
                self._progress_tracker.update(job_id, progress)
                with self._jobs_lock:
                    if job_id in self._jobs:
                        self._jobs[job_id].progress = progress

            result = provider.download(
                request=request,
                output_dir=request.output_directory,
                progress_callback=progress_callback,
            )

            result.job_id = job_id

            with self._jobs_lock:
                if job_id in self._jobs:
                    self._jobs[job_id].result = result

            final_progress = DownloadProgress(
                job_id=job_id,
                status=DownloadStatus.COMPLETED,
                bytes_downloaded=result.file_size_bytes or 0,
                total_bytes=result.file_size_bytes,
                percent=100.0,
            )
            self._progress_tracker.update(job_id, final_progress)

            with self._jobs_lock:
                if job_id in self._jobs:
                    self._jobs[job_id].progress = final_progress
                    self._jobs[job_id].updated_at = datetime.now()

            # Log with ASCII-safe title to avoid UnicodeEncodeError on Windows console (cp1252)
            safe_title = result.title.encode("ascii", "replace").decode("ascii")
            logger.info(
                "Download complete: %s -> %s (%s)",
                job_id[:8],
                safe_title,
                result.file_size_human,
            )
            return result
        except DownloadCancelledError:
            with self._cancelled_lock:
                self._cancelled_job_ids.discard(job_id)
            with self._jobs_lock:
                is_pause = job_id in self._paused_job_ids
                if is_pause:
                    self._paused_job_ids.discard(job_id)
            if is_pause:
                self._update_job_status(job_id, DownloadStatus.PAUSED)
                logger.info("Job %s paused (was active)", job_id[:8])
            else:
                self._update_job_status(job_id, DownloadStatus.CANCELLED)
                logger.info("Job %s cancelled (was active)", job_id[:8])
            raise
        except (DownloadError, ProviderError) as exc:
            logger.error("Download failed: %s -> %s", job_id[:8], safe_str(str(exc)))
            self._update_job_status(job_id, DownloadStatus.FAILED, error_message=str(exc))
            raise
        except Exception as exc:
            logger.error("Unexpected download error: %s -> %s", job_id[:8], safe_str(str(exc)))
            self._update_job_status(job_id, DownloadStatus.FAILED, error_message=str(exc))
            raise DownloadError(str(exc), url=request.url) from exc

    def _on_download_complete(self, job_id: str, future: Future[DownloadResult]) -> None:
        """Callback when a download future completes."""
        with self._active_lock:
            self._active_count = max(0, self._active_count - 1)

        with self._jobs_lock:
            self._futures.pop(job_id, None)
            self._paused_job_ids.discard(job_id)
        with self._cancelled_lock:
            self._cancelled_job_ids.discard(job_id)

        try:
            exc = future.exception()
            if exc:
                logger.debug("Job %s completed with error: %s", job_id[:8], exc)
        except Exception:
            pass

    def _update_job_status(
        self,
        job_id: str,
        status: DownloadStatus,
        error_message: str | None = None,
    ) -> None:
        """Update a job's status and progress."""
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job:
                job.progress.status = status
                job.updated_at = datetime.now()
                if error_message and job.result:
                    job.result.error_message = error_message
                elif error_message:
                    job.result = DownloadResult(
                        job_id=job_id,
                        url=job.request.url,
                        provider=ProviderType.GENERIC,
                        status=status,
                        title="Unknown",
                        media_type=job.request.media_type,
                        error_message=error_message,
                    )

        self._progress_tracker.update(
            job_id,
            DownloadProgress(job_id=job_id, status=status),
        )

