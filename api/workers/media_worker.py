"""MediaForge background media worker (STEP 03-B).

Consumes offline clip extraction and video merging work items from PostgreSQL,
manages worker leases, executes FFmpeg in bounded concurrency, tracks progress,
coordinates atomic usage ledger recording, and safely handles cancellation and recovery.

Run with:
    python -m api.workers.media_worker
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Load environment configuration
from src.env_loader import load_project_dotenv

load_project_dotenv()

from api.config import get_api_settings
from api.database.connection import close_database, get_session_factory, init_database
from api.database.models import APIKeyDB, MediaArtifactDB, MediaWorkItemDB, UserDB
from api.dependencies import AuthMethod, CurrentUser
from api.security.media_storage import (
    MediaSecurityError,
    generate_safe_output_path,
    get_ffmpeg_semaphore,
    get_job_staging_path,
    get_tenant_media_dir,
    get_tenant_storage_root,
    reconcile_orphaned_staging_files,
    reconcile_orphaned_staging_files_safe,
    resolve_and_authorize_clip_source,
    validate_safe_filename,
)
from api.services.artifact_service import (
    complete_artifact_in_fresh_session,
    compute_sha256,
    create_artifact_record,
    fail_artifact_in_fresh_session,
    resolve_source_media_reference,
    safe_scalar_one_or_none,
    sanitize_media_error,
    verify_source_media_authorization,
)
from api.services.job_service import (
    claim_next_job,
    complete_job_and_artifact_atomic,
    complete_job_succeeded,
    fail_job,
    heartbeat_and_check_cancellation,
    mark_job_cancelled,
    recover_expired_leases,
)
from src.clip.extractor import ClipRequest, get_clip_extractor
from src.clip.merger import MergeRequest, get_clip_merger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Worker] %(message)s",
)
logger = logging.getLogger("media_worker")


def is_retryable_error(exc: Exception) -> bool:
    """Classify whether a failure is transient/retryable or permanent.

    Prefers typed exceptions for security and infrastructure faults.
    """
    # 1. Typed security and client exceptions -> Non-retryable
    if isinstance(exc, MediaSecurityError):
        return False
    if isinstance(exc, HTTPException):
        if 400 <= exc.status_code < 500:
            return False
        if exc.status_code in (503, 504):
            return True

    # 2. Typed database & Redis connection / deadlock exceptions -> Retryable
    try:
        from sqlalchemy.exc import DBAPIError, OperationalError
        if isinstance(exc, (OperationalError, DBAPIError)):
            return True
    except ImportError:
        pass

    try:
        import redis.exceptions
        if isinstance(exc, (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)):
            return True
    except ImportError:
        pass

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True

    # 3. Permanent / deterministic errors -> Non-retryable
    text = str(exc).lower()
    permanent_keywords = [
        "not found",
        "invalid",
        "unsupported",
        "cannot merge",
        "exceeds duration",
        "permission denied",
        "bad request",
        "no such file",
        "security",
        "deactivated",
        "revoked",
        "expired",
        "unauthorized",
        "does not exist",
        "ambiguous",
    ]
    if any(k in text for k in permanent_keywords):
        return False

    # 4. Transient / infrastructure errors -> Retryable
    retryable_keywords = [
        "connection",
        "timeout",
        "temporarily unavailable",
        "deadlock",
        "concurrency limit",
        "busy",
    ]
    if any(k in text for k in retryable_keywords):
        return True

    return False


async def revalidate_worker_requester(db: AsyncSession, job: MediaWorkItemDB) -> CurrentUser:
    """Revalidate that the job's requester user and/or API key are valid, active, and authorized.

    Returns an authoritative CurrentUser. Fails closed if the user is deleted/inactive,
    or if the API key is revoked/inactive.
    """
    user_id = job.requester_user_id
    api_key_id = job.requester_api_key_id
    tenant_id = job.tenant_id

    role = "member"

    # 1. Authoritative UserDB check
    if user_id and not user_id.startswith("service:"):
        u_res = await db.execute(
            select(UserDB).where(UserDB.id == user_id, UserDB.tenant_id == tenant_id)
        )
        u_row = await safe_scalar_one_or_none(u_res)
        if not u_row:
            raise MediaSecurityError(f"Job requester user '{user_id}' does not exist in tenant '{tenant_id}'")
        if u_row.is_active is False:
            raise MediaSecurityError(f"Job requester user '{user_id}' is deactivated or suspended")
        role = u_row.role

    # 2. Authoritative APIKeyDB check
    if api_key_id:
        ak_res = await db.execute(
            select(APIKeyDB).where(APIKeyDB.id == api_key_id, APIKeyDB.tenant_id == tenant_id)
        )
        ak_row = await safe_scalar_one_or_none(ak_res)
        if not ak_row:
            raise MediaSecurityError(f"Job requester API key '{api_key_id}' does not exist in tenant '{tenant_id}'")
        if ak_row.is_revoked or ak_row.revoked_at is not None:
            raise MediaSecurityError(f"Job requester API key '{api_key_id}' has been revoked")
        if ak_row.is_active is False:
            raise MediaSecurityError(f"Job requester API key '{api_key_id}' is deactivated")
        if ak_row.expires_at and ak_row.expires_at < datetime.utcnow():
            raise MediaSecurityError(f"Job requester API key '{api_key_id}' has expired")

        permissions = ak_row.permissions or []
        required_scopes = {"clips", "clips:write", "media", "media:write", "media:create", "*"}
        if permissions and not any(p in required_scopes for p in permissions):
            raise MediaSecurityError(f"Requester API key '{api_key_id}' lacks required clip/media scopes")

    return CurrentUser(
        user_id=user_id or f"service:{api_key_id}",
        tenant_id=tenant_id,
        role=role,
        api_key_id=api_key_id,
        auth_method=AuthMethod.API_KEY if api_key_id else AuthMethod.JWT,
    )


def _terminate_process_group(proc: subprocess.Popen) -> None:
    """Terminate process group gracefully with SIGTERM, then escalate to SIGKILL."""
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt" and hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (OSError, ProcessLookupError):
        pass

    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt" and hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass


class MediaWorker:
    """Durable PostgreSQL-backed background media worker."""

    def __init__(
        self,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
        lease_duration: int = 30,
        concurrency: int = 2,
        session_factory: Optional[Any] = None,
    ):
        hostname = socket.gethostname()
        pid = os.getpid()
        short_id = uuid.uuid4().hex[:6]
        self.worker_id = worker_id or f"worker-{hostname}-{pid}-{short_id}"
        self.poll_interval = poll_interval
        self.lease_duration = lease_duration
        self.semaphore = asyncio.Semaphore(concurrency)
        self.session_factory = session_factory
        self.running = False
        self.jobs_processed = 0
        self.active_tasks: set[asyncio.Task] = set()
        self.active_job_cancel_events: dict[str, asyncio.Event] = {}
        self.active_job_processes: dict[str, list[subprocess.Popen]] = {}

    async def start(self) -> None:
        """Initialize resources and enter main polling/processing loop."""
        settings = get_api_settings()
        logger.info("Starting MediaWorker ID: %s (PID: %d)...", self.worker_id, os.getpid())

        # Initialize database engine (never auto-runs migrations)
        await init_database(
            database_url=settings.database_url,
            schema=settings.database_schema,
            pool_size=5,
            max_overflow=10,
        )

        self.running = True
        logger.info("MediaWorker operational. Listening for work items...")

        # Start periodic recovery background task
        recovery_task = asyncio.create_task(self._recovery_loop())
        self.active_tasks.add(recovery_task)

        # Start recurring scheduled transitions background task (STEP 04-B1)
        transitions_task = asyncio.create_task(self._subscription_transitions_loop(initial_delay_seconds=5.0))
        self.active_tasks.add(transitions_task)

        # Start optional Redis wake-up listener
        redis_task = asyncio.create_task(self._redis_wake_loop())
        self.active_tasks.add(redis_task)

        # Main polling loop
        session_factory = self.session_factory or get_session_factory()
        while self.running:
            try:
                # Touch healthcheck heartbeat file
                try:
                    Path("/tmp/worker_heartbeat").touch()
                except Exception:
                    pass

                acquired_slot = False
                try:
                    # Non-blocking concurrency check
                    await asyncio.wait_for(self.semaphore.acquire(), timeout=0.1)
                    acquired_slot = True
                except asyncio.TimeoutError:
                    await asyncio.sleep(self.poll_interval)
                    continue

                async with session_factory() as db:
                    job = await claim_next_job(
                        db,
                        worker_id=self.worker_id,
                        lease_duration_seconds=self.lease_duration,
                    )

                if job is not None:
                    logger.info("Claimed job %s (type: %s, tenant: %s)", job.id, job.operation_type, job.tenant_id)
                    job_task = asyncio.create_task(self._process_job_wrapper(job))
                    self.active_tasks.add(job_task)
                    job_task.add_done_callback(self.active_tasks.discard)
                else:
                    self.semaphore.release()
                    await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in worker polling loop: %s", exc)
                if acquired_slot:
                    self.semaphore.release()
                await asyncio.sleep(self.poll_interval)

        logger.info("MediaWorker loop stopped. Draining active tasks...")
        if self.active_tasks:
            await asyncio.gather(*[t for t in self.active_tasks if not t.done()], return_exceptions=True)
        await close_database()
        logger.info("MediaWorker shutdown complete. Processed %d jobs.", self.jobs_processed)

    def stop(self) -> None:
        """Signal worker to stop gracefully."""
        logger.info("Stop requested for MediaWorker %s", self.worker_id)
        self.running = False

    async def _recovery_loop(self) -> None:
        """Periodically audit and recover expired leases and clean abandoned staging files."""
        session_factory = self.session_factory or get_session_factory()
        while self.running:
            try:
                await asyncio.sleep(15.0)
                if not self.running:
                    break
                async with session_factory() as db:
                    recovered = await recover_expired_leases(db)
                    if recovered > 0:
                        logger.info("Recovered %d orphaned/expired jobs", recovered)
                    cleaned_report = await reconcile_orphaned_staging_files_safe(db=db, max_age_seconds=1800)
                    if cleaned_report.deleted_count > 0:
                        logger.info("Cleaned %d abandoned crash-orphaned staging files", cleaned_report.deleted_count)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Recovery loop error: %s", exc)

    async def _subscription_transitions_loop(self, initial_delay_seconds: float = 5.0) -> None:
        """Periodically evaluate and process due commercial subscription transitions with rollout guard (STEP 04-B1)."""
        from api.services.subscription_lifecycle_service import (
            get_scheduler_config,
            process_due_subscription_transitions,
            update_scheduler_state,
        )

        session_factory = self.session_factory or get_session_factory()
        # Stagger startup if specified
        if initial_delay_seconds > 0:
            await asyncio.sleep(initial_delay_seconds)

        while self.running:
            try:
                poll_interval = 60.0
                executed_count = 0
                state_status = "idle"

                async with session_factory() as db:
                    cfg = await get_scheduler_config(db)
                    poll_interval = float(cfg.get("poll_interval_seconds", 60.0))
                    is_paused = bool(cfg.get("paused", False))

                    if not is_paused:
                        results = await process_due_subscription_transitions(
                            db,
                            enforce_rollout_guard=True,
                        )
                        await db.commit()
                        executed_count = len(results)
                        if executed_count > 0:
                            logger.info("Automatically executed %d due subscription transitions", executed_count)
                            state_status = "active"
                    else:
                        state_status = "paused"

                    await update_scheduler_state(
                        db,
                        {
                            "last_scan_at": datetime.utcnow().isoformat(),
                            "last_executed_count": executed_count,
                            "worker_id": self.worker_id,
                            "status": state_status,
                        },
                    )
                    await db.commit()

                # Sleep interval with periodic check of self.running
                sleep_remaining = poll_interval
                while sleep_remaining > 0 and self.running:
                    step = min(1.0, sleep_remaining)
                    await asyncio.sleep(step)
                    sleep_remaining -= step

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in subscription transitions scheduler loop: %s", exc)
                try:
                    async with session_factory() as db:
                        await update_scheduler_state(
                            db,
                            worker_id=self.worker_id,
                            status="error",
                            executed_count=0,
                            scan_succeeded=False,
                            error_message=str(exc),
                        )
                        await db.commit()
                except Exception:
                    pass
                await asyncio.sleep(15.0)

    async def _redis_wake_loop(self) -> None:
        """Listen for instant Redis job notifications (optional wakeup)."""
        settings = get_api_settings()
        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                password=settings.redis_password,
                db=settings.redis_db,
            )
            pubsub = r.pubsub()
            await pubsub.subscribe("media_jobs:new", "media_jobs:cancel")
            logger.info("Worker %s subscribed to Redis channels media_jobs:new, media_jobs:cancel", self.worker_id)
            while self.running:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    ch = msg.get("channel")
                    data = msg.get("data")
                    data_str = data.decode("utf-8") if isinstance(data, bytes) else str(data)
                    ch_str = ch.decode("utf-8") if isinstance(ch, bytes) else str(ch)
                    if ch_str == "media_jobs:cancel":
                        if data_str in self.active_job_cancel_events:
                            logger.info("Instant Redis cancellation observed for running job %s", data_str)
                            self.active_job_cancel_events[data_str].set()
                            for p in self.active_job_processes.get(data_str, []):
                                _terminate_process_group(p)
                await asyncio.sleep(0.5)
            await pubsub.unsubscribe("media_jobs:new", "media_jobs:cancel")
            await r.aclose()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Redis wake loop unavailable (%s); relying on database polling", exc)

    async def _process_job_wrapper(self, job: MediaWorkItemDB) -> None:
        """Wrapper ensuring semaphore release upon completion."""
        try:
            await self._execute_job(job)
            self.jobs_processed += 1
        finally:
            self.semaphore.release()

    async def _execute_job(self, job: MediaWorkItemDB) -> None:
        """Execute a claimed media work item with heartbeat and cancellation checks."""
        job_id = job.id
        tenant_id = job.tenant_id
        op_type = job.operation_type
        payload = job.request_payload or {}
        session_factory = self.session_factory or get_session_factory()

        stop_heartbeat = asyncio.Event()
        cancel_signaled = asyncio.Event()
        self.active_job_cancel_events[job_id] = cancel_signaled
        self.active_job_processes[job_id] = []

        # Background heartbeat & cancellation monitor
        async def _heartbeat_worker():
            while not stop_heartbeat.is_set():
                try:
                    await asyncio.sleep(0.5)
                    if stop_heartbeat.is_set():
                        break
                    async with session_factory() as db:
                        valid, cancel_req = await heartbeat_and_check_cancellation(
                            db,
                            job_id=job_id,
                            worker_id=self.worker_id,
                            lease_duration_seconds=self.lease_duration,
                        )
                        if not valid:
                            logger.warning("Worker %s fenced out of job %s", self.worker_id, job_id)
                            cancel_signaled.set()
                            for p in self.active_job_processes.get(job_id, []):
                                _terminate_process_group(p)
                            break
                        if cancel_req:
                            logger.info("Cancellation observed for running job %s", job_id)
                            cancel_signaled.set()
                            for p in self.active_job_processes.get(job_id, []):
                                _terminate_process_group(p)
                            break
                except asyncio.CancelledError:
                    break
                except Exception as hb_exc:
                    logger.warning("Heartbeat failure for job %s: %s", job_id, hb_exc)

        hb_task = asyncio.create_task(_heartbeat_worker())
        partial_output: Optional[Path] = None

        try:
            if op_type == "clip_extract":
                await self._execute_clip(job, payload, cancel_signaled)
            elif op_type == "merge":
                await self._execute_merge(job, payload, cancel_signaled)
            else:
                async with session_factory() as db:
                    await fail_job(
                        db,
                        job_id=job_id,
                        worker_id=self.worker_id,
                        error_code="UNSUPPORTED_OPERATION",
                        error_message=f"Unsupported operation type '{op_type}'",
                        retryable=False,
                    )
        except asyncio.CancelledError:
            logger.info("Job %s execution cancelled via task cancellation", job_id)
            async with session_factory() as db:
                await mark_job_cancelled(db, job_id, self.worker_id)
        except Exception as exc:
            retryable = is_retryable_error(exc)
            logger.error("Job %s failed (%s, retryable=%s): %s", job_id, type(exc).__name__, retryable, exc)
            async with session_factory() as db:
                await fail_job(
                    db,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    retryable=retryable,
                )
        finally:
            stop_heartbeat.set()
            hb_task.cancel()
            self.active_job_cancel_events.pop(job_id, None)
            self.active_job_processes.pop(job_id, None)

    async def _execute_clip(
        self,
        job: MediaWorkItemDB,
        payload: dict[str, Any],
        cancel_signaled: asyncio.Event,
    ) -> None:
        """Execute clip extraction work item."""
        job_id = job.id
        tenant_id = job.tenant_id
        session_factory = self.session_factory or get_session_factory()

        source = payload.get("source")
        start_time = payload.get("start_time")
        end_time = payload.get("end_time")
        mode = payload.get("mode", "precise")
        output_format = payload.get("output_format", "mp4")

        # 1. Update progress: 10% (preparing inputs)
        async with session_factory() as db:
            await heartbeat_and_check_cancellation(
                db, job_id, self.worker_id, self.lease_duration, progress_percent=10.0
            )

        if cancel_signaled.is_set():
            async with session_factory() as db:
                await mark_job_cancelled(db, job_id, self.worker_id)
            return

        # 2. Resolve safe paths and attempt-specific staging
        is_url = source.startswith("http://") or source.startswith("https://")
        if not is_url:
            async with session_factory() as db:
                authoritative_user = await revalidate_worker_requester(db, job)
                source_to_resolve = payload.get("canonical_source_id") or source
                resolved_source = await resolve_source_media_reference(db, source_to_resolve, tenant_id, user=authoritative_user)
                await verify_source_media_authorization(db, resolved_source, authoritative_user)
            abs_src = resolve_and_authorize_clip_source(
                source=resolved_source,
                tenant_id=tenant_id,
                user_id=job.requester_user_id,
                is_admin=authoritative_user.is_admin_or_owner,
            )
            if not abs_src.is_file():
                raise MediaSecurityError(f"Source media file '{resolved_source}' does not exist on disk")
        else:
            abs_src = None

        staging_path = get_job_staging_path(
            tenant_id=tenant_id,
            job_id=job_id,
            attempt_count=job.attempt_count or 1,
            extension=output_format,
        )
        abs_output, rel_output, cid = generate_safe_output_path(
            tenant_id=tenant_id,
            subfolder="clips",
            prefix="clip",
            extension=output_format,
        )

        # 3. Create pending artifact record
        async with session_factory() as db:
            await create_artifact_record(
                db=db,
                artifact_id=cid,
                tenant_id=tenant_id,
                user_id=job.requester_user_id,
                api_key_id=job.requester_api_key_id,
                artifact_type="clip",
                storage_path=rel_output,
                filename=abs_output.name,
                format=output_format,
                status="processing",
                input_sources=[source],
                metadata_json={
                    "mode": mode,
                    "start_time": start_time,
                    "end_time": end_time,
                    "job_id": job_id,
                },
            )

        # 4. Update progress: 30% (running FFmpeg)
        async with session_factory() as db:
            await heartbeat_and_check_cancellation(
                db, job_id, self.worker_id, self.lease_duration, progress_percent=30.0
            )

        def on_proc(p):
            if job_id in self.active_job_processes:
                self.active_job_processes[job_id].append(p)

        def is_cancelled():
            return cancel_signaled.is_set()

        clip_req = ClipRequest(
            source=str(abs_src) if not is_url else source,
            start_time=start_time,
            end_time=end_time,
            output_path=str(staging_path),
            output_format=output_format,
            mode=mode,
            resolution=payload.get("resolution"),
            video_bitrate=payload.get("video_bitrate"),
            audio_bitrate=payload.get("audio_bitrate", "192k"),
            clip_id=cid,
            is_url=is_url,
            is_cancelled=is_cancelled,
            on_proc_started=on_proc,
        )

        extractor = get_clip_extractor()
        t0 = time.perf_counter()

        # Execute extraction in thread with cancellation check
        loop = asyncio.get_running_loop()
        extract_future = loop.run_in_executor(None, extractor.extract, clip_req)

        while not extract_future.done():
            if cancel_signaled.is_set():
                for p in self.active_job_processes.get(job_id, []):
                    _terminate_process_group(p)
                extract_future.cancel()
                if staging_path.exists():
                    staging_path.unlink(missing_ok=True)
                async with session_factory() as db:
                    await fail_artifact_in_fresh_session(cid, "Cancelled by user")
                    await mark_job_cancelled(db, job_id, self.worker_id)
                return
            await asyncio.sleep(0.5)

        result = await extract_future
        processing_time_ms = max(0, int((time.perf_counter() - t0) * 1000))

        if cancel_signaled.is_set():
            for p in self.active_job_processes.get(job_id, []):
                _terminate_process_group(p)
            if staging_path.exists():
                staging_path.unlink(missing_ok=True)
            async with session_factory() as db:
                await fail_artifact_in_fresh_session(cid, "Cancelled by user")
                await mark_job_cancelled(db, job_id, self.worker_id)
            return

        if not result.success:
            if staging_path.exists():
                staging_path.unlink(missing_ok=True)
            async with session_factory() as db:
                await fail_artifact_in_fresh_session(cid, result.error or "Extraction failed")
                await fail_job(
                    db,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    error_code="FFMPEG_EXTRACTION_FAILED",
                    error_message=result.error or "Extraction failed",
                    retryable=False,
                )
            return

        # 5. Update progress: 80% (verifying output & checksum on staged file)
        sha256 = compute_sha256(staging_path) if staging_path.exists() else ""
        file_size_bytes = staging_path.stat().st_size if staging_path.exists() else (result.file_size_bytes or 0)

        async with session_factory() as db:
            valid, cancel_req = await heartbeat_and_check_cancellation(
                db, job_id, self.worker_id, self.lease_duration, progress_percent=80.0
            )
            if cancel_req or not valid or cancel_signaled.is_set():
                for p in self.active_job_processes.get(job_id, []):
                    _terminate_process_group(p)
                if staging_path.exists():
                    staging_path.unlink(missing_ok=True)
                async with session_factory() as db:
                    await fail_artifact_in_fresh_session(cid, "Cancelled by user")
                    await mark_job_cancelled(db, job_id, self.worker_id)
                return

        # 6. Atomic in-transaction completion of job, artifact & commercial usage
        usage_kwargs = {
            "tenant_id": tenant_id,
            "event_type": "clip",
            "idempotency_key": f"clip:{cid}",
            "resource_id": cid,
            "quantity": 1.0,
            "unit": "count",
            "bytes": file_size_bytes,
            "duration_seconds": result.duration_seconds or 0.0,
            "user_id": job.requester_user_id,
            "api_key_id": job.requester_api_key_id,
            "auth_method": payload.get("auth_method", "worker"),
            "metadata_json": {
                "source": source,
                "mode": mode,
                "processing_time_ms": processing_time_ms,
                "worker_id": self.worker_id,
                "job_id": job_id,
            },
        }

        # 6. Atomic publication and in-transaction completion of job, artifact & commercial usage
        # Physical file is atomically published to abs_output BEFORE DB transitions commit!
        async with session_factory() as db:
            succeeded = await complete_job_and_artifact_atomic(
                db=db,
                job_id=job_id,
                worker_id=self.worker_id,
                artifact_id=cid,
                duration_seconds=result.duration_seconds or 0.0,
                file_size_bytes=file_size_bytes,
                sha256_checksum=sha256,
                usage_event_kwargs=usage_kwargs,
                expected_attempt=job.attempt_count,
                staging_path=staging_path,
                abs_output=abs_output,
            )

        if not succeeded:
            logger.warning("Fencing, cancellation, or publication failure prevented job %s atomic completion; cleaning up staging", job_id)
            # CRITICAL FILESYSTEM FENCING: Clean up ONLY the losing attempt's own staging file.
            # Never touch abs_output which may belong to a winning attempt!
            if staging_path.exists():
                staging_path.unlink(missing_ok=True)
            async with session_factory() as db:
                await fail_artifact_in_fresh_session(cid, "Fenced: lease lost, job cancelled, or publication failed")
            return

        logger.info("Successfully completed clip job %s -> Artifact %s (%s, %.2fs)", job_id, cid, abs_output.name, result.duration_seconds)

    async def _execute_merge(
        self,
        job: MediaWorkItemDB,
        payload: dict[str, Any],
        cancel_signaled: asyncio.Event,
    ) -> None:
        """Execute video merge work item."""
        job_id = job.id
        tenant_id = job.tenant_id
        session_factory = self.session_factory or get_session_factory()

        clips = payload.get("clips", [])
        output_format = payload.get("output_format", "mp4")
        mode = payload.get("mode", "auto")

        # 1. Update progress: 10% (preparing inputs)
        async with session_factory() as db:
            await heartbeat_and_check_cancellation(
                db, job_id, self.worker_id, self.lease_duration, progress_percent=10.0
            )

        if cancel_signaled.is_set():
            async with session_factory() as db:
                await mark_job_cancelled(db, job_id, self.worker_id)
            return

        # 2. Resolve safe paths and attempt-specific staging
        resolved_clips: list[str] = []
        async with session_factory() as db:
            authoritative_user = await revalidate_worker_requester(db, job)
            for c_ref in clips:
                res_c = await resolve_source_media_reference(db, c_ref, tenant_id, user=authoritative_user)
                await verify_source_media_authorization(db, res_c, authoritative_user)
                abs_p = resolve_and_authorize_clip_source(
                    source=res_c,
                    tenant_id=tenant_id,
                    user_id=job.requester_user_id,
                    is_admin=authoritative_user.is_admin_or_owner,
                )
                if not abs_p.is_file():
                    raise MediaSecurityError(f"Source clip file '{res_c}' does not exist on disk")
                resolved_clips.append(str(abs_p))

        staging_path = get_job_staging_path(
            tenant_id=tenant_id,
            job_id=job_id,
            attempt_count=job.attempt_count or 1,
            extension=output_format,
        )
        abs_output, rel_output, mid = generate_safe_output_path(
            tenant_id=tenant_id,
            subfolder="merged",
            prefix=f"merged_{len(clips)}clips",
            extension=output_format,
        )

        # 3. Create pending artifact record
        async with session_factory() as db:
            await create_artifact_record(
                db=db,
                artifact_id=mid,
                tenant_id=tenant_id,
                user_id=job.requester_user_id,
                api_key_id=job.requester_api_key_id,
                artifact_type="merge",
                storage_path=rel_output,
                filename=abs_output.name,
                format=output_format,
                status="processing",
                input_sources=clips,
                metadata_json={
                    "mode": mode,
                    "clip_count": len(clips),
                    "job_id": job_id,
                },
            )

        # 4. Update progress: 30% (running FFmpeg)
        async with session_factory() as db:
            await heartbeat_and_check_cancellation(
                db, job_id, self.worker_id, self.lease_duration, progress_percent=30.0
            )

        def on_proc(p):
            if job_id in self.active_job_processes:
                self.active_job_processes[job_id].append(p)

        def is_cancelled():
            return cancel_signaled.is_set()

        merge_req = MergeRequest(
            clips=resolved_clips,
            output_path=str(staging_path),
            output_format=output_format,
            mode=mode,
            merge_id=mid,
            is_cancelled=is_cancelled,
            on_proc_started=on_proc,
        )

        merger = get_clip_merger()
        t0 = time.perf_counter()

        loop = asyncio.get_running_loop()
        merge_future = loop.run_in_executor(None, merger.merge, merge_req)

        while not merge_future.done():
            if cancel_signaled.is_set():
                for p in self.active_job_processes.get(job_id, []):
                    _terminate_process_group(p)
                merge_future.cancel()
                if staging_path.exists():
                    staging_path.unlink(missing_ok=True)
                async with session_factory() as db:
                    await fail_artifact_in_fresh_session(mid, "Cancelled by user")
                    await mark_job_cancelled(db, job_id, self.worker_id)
                return
            await asyncio.sleep(0.5)

        result = await merge_future
        processing_time_ms = max(0, int((time.perf_counter() - t0) * 1000))

        if cancel_signaled.is_set():
            for p in self.active_job_processes.get(job_id, []):
                _terminate_process_group(p)
            if staging_path.exists():
                staging_path.unlink(missing_ok=True)
            async with session_factory() as db:
                await fail_artifact_in_fresh_session(mid, "Cancelled by user")
                await mark_job_cancelled(db, job_id, self.worker_id)
            return

        if not result.success:
            if staging_path.exists():
                staging_path.unlink(missing_ok=True)
            async with session_factory() as db:
                await fail_artifact_in_fresh_session(mid, result.error or "Merge failed")
                await fail_job(
                    db,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    error_code="FFMPEG_MERGE_FAILED",
                    error_message=result.error or "Merge failed",
                    retryable=False,
                )
            return

        # 5. Update progress: 80% (verifying output & checksum on staged file)
        sha256 = compute_sha256(staging_path) if staging_path.exists() else ""
        file_size_bytes = staging_path.stat().st_size if staging_path.exists() else (result.file_size_bytes or 0)

        async with session_factory() as db:
            valid, cancel_req = await heartbeat_and_check_cancellation(
                db, job_id, self.worker_id, self.lease_duration, progress_percent=80.0
            )
            if cancel_req or not valid or cancel_signaled.is_set():
                for p in self.active_job_processes.get(job_id, []):
                    _terminate_process_group(p)
                if staging_path.exists():
                    staging_path.unlink(missing_ok=True)
                async with session_factory() as db:
                    await fail_artifact_in_fresh_session(mid, "Cancelled by user")
                    await mark_job_cancelled(db, job_id, self.worker_id)
                return

        # 6. Atomic in-transaction completion of job, artifact & commercial usage
        usage_kwargs = {
            "tenant_id": tenant_id,
            "event_type": "merge",
            "idempotency_key": f"merge:{mid}",
            "resource_id": mid,
            "quantity": 1.0,
            "unit": "count",
            "bytes": file_size_bytes,
            "duration_seconds": result.total_duration_seconds or 0.0,
            "user_id": job.requester_user_id,
            "api_key_id": job.requester_api_key_id,
            "auth_method": payload.get("auth_method", "worker"),
            "metadata_json": {
                "clip_count": result.clip_count,
                "clips_used": clips,
                "processing_time_ms": processing_time_ms,
                "worker_id": self.worker_id,
                "job_id": job_id,
            },
        }

        # 6. Atomic publication and in-transaction completion of job, artifact & commercial usage
        # Physical file is atomically published to abs_output BEFORE DB transitions commit!
        async with session_factory() as db:
            succeeded = await complete_job_and_artifact_atomic(
                db=db,
                job_id=job_id,
                worker_id=self.worker_id,
                artifact_id=mid,
                duration_seconds=result.total_duration_seconds or 0.0,
                file_size_bytes=file_size_bytes,
                sha256_checksum=sha256,
                usage_event_kwargs=usage_kwargs,
                expected_attempt=job.attempt_count,
                staging_path=staging_path,
                abs_output=abs_output,
            )

        if not succeeded:
            logger.warning("Fencing, cancellation, or publication failure prevented job %s atomic completion; cleaning up staging", job_id)
            # CRITICAL FILESYSTEM FENCING: Clean up ONLY the losing attempt's own staging file.
            # Never touch abs_output which may belong to a winning attempt!
            if staging_path.exists():
                staging_path.unlink(missing_ok=True)
            async with session_factory() as db:
                await fail_artifact_in_fresh_session(mid, "Fenced: lease lost, job cancelled, or publication failed")
            return

        logger.info("Successfully completed merge job %s -> Artifact %s (%s, %.2fs)", job_id, mid, abs_output.name, result.total_duration_seconds)


def run_worker_main():
    """Entry point for standalone worker process."""
    worker = MediaWorker()

    def _sig_handler(sig, frame):
        logger.info("Received signal %s. Shutting down worker...", sig)
        worker.stop()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run_worker_main()
