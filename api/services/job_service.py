"""Durable background media work items service (STEP 03-B).

Implements PostgreSQL-authoritative job submission, atomic claims with row locking,
stale-worker fencing, progress checkpoints, retry management, and cancellation.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.database.connection import get_session_factory
from api.database.models import MediaArtifactDB, MediaWorkItemDB
from api.dependencies import CurrentUser
from api.schemas.artifacts import MediaArtifactResponse
from api.schemas.clips import ClipExtractRequest
from api.schemas.jobs import JobDetailResponse
from api.security.media_storage import (
    _SAFE_ID_PATTERN,
    generate_safe_output_path,
    get_tenant_media_dir,
    get_tenant_storage_root,
    resolve_and_authorize_clip_source,
    validate_safe_filename,
)
from api.services.artifact_service import (
    format_bytes,
    model_to_response,
    resolve_source_media_reference,
    safe_scalar_first,
    safe_scalar_one_or_none,
    safe_scalars_all,
    sanitize_media_error,
    verify_source_media_authorization,
)

logger = logging.getLogger(__name__)

# Test hook
async_session_factory: Any = None


class JobIdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused with conflicting operation parameters."""

    pass


def _utcnow() -> datetime:
    """Return timezone-naive UTC datetime for consistent database timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def notify_new_job_event(job_id: str) -> None:
    """Publish a wake-up signal to Redis for fast worker pickup (optional optimization)."""
    try:
        from api.config import get_api_settings
        import redis.asyncio as aioredis

        settings = get_api_settings()
        r = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            db=settings.redis_db,
            socket_timeout=1.0,
        )
        await r.publish("media_jobs:new", job_id)
        await r.aclose()
    except Exception as exc:
        logger.debug("Redis notification skipped: %s", exc)


async def notify_cancel_job_event(job_id: str) -> None:
    """Publish a cancellation signal to Redis for fast worker termination."""
    try:
        from api.config import get_api_settings
        import redis.asyncio as aioredis

        settings = get_api_settings()
        r = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            db=settings.redis_db,
            socket_timeout=1.0,
        )
        await r.publish("media_jobs:cancel", job_id)
        await r.aclose()
    except Exception as exc:
        logger.debug("Redis cancel notification skipped: %s", exc)


def work_item_to_detail_response(item: MediaWorkItemDB, user: Optional[CurrentUser] = None) -> JobDetailResponse:
    """Convert an ORM MediaWorkItemDB to JobDetailResponse schema with role-based field sanitization."""
    result_art_resp = None
    if item.result_artifact:
        result_art_resp = model_to_response(item.result_artifact)

    # Sanitize internal worker lease fields for regular members
    is_admin = user.is_admin_or_owner if user else True
    claimed_by = item.claimed_by if is_admin else ("worker-active" if item.claimed_by else None)

    return JobDetailResponse(
        job_id=item.id,
        tenant_id=item.tenant_id,
        operation_type=item.operation_type,
        status=item.status,
        progress_percent=float(item.progress_percent or 0.0),
        attempt_count=int(item.attempt_count or 0),
        max_attempts=int(item.max_attempts or 3),
        claimed_by=claimed_by,
        lease_expires_at=item.lease_expires_at if is_admin else None,
        last_heartbeat_at=item.last_heartbeat_at if is_admin else None,
        available_at=item.available_at,
        cancellation_requested=item.cancellation_requested_at is not None,
        result_artifact_id=item.result_artifact_id,
        result_artifact=result_art_resp,
        error_code=item.sanitized_error_code,
        error_message=item.sanitized_error_message,
        request_payload=dict(item.request_payload or {}),
        created_at=item.created_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
        updated_at=item.updated_at,
    )


# ── Submission Handlers ──


async def submit_clip_job(
    db: AsyncSession,
    user: CurrentUser,
    request: ClipExtractRequest,
    idempotency_key: Optional[str] = None,
) -> MediaWorkItemDB:
    """Validate clip parameters and submit durable background work item."""
    # 1. Authorize source media within tenant boundaries
    is_url = request.source.startswith("http://") or request.source.startswith("https://")
    canonical_source_id = request.source
    if not is_url:
        resolved_source = await resolve_source_media_reference(db, request.source, user.tenant_id, user)
        await verify_source_media_authorization(db, resolved_source, user)
        resolve_and_authorize_clip_source(
            source=resolved_source,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            is_admin=user.is_admin_or_owner,
        )
        request.source = resolved_source

    # 2. Derive stable idempotency key if not explicitly supplied
    if not idempotency_key or not idempotency_key.strip():
        hash_input = f"{user.tenant_id}:clip:{request.source}:{request.start_time}:{request.end_time}:{request.mode}:{request.output_format}"
        clean_key = f"clip:{hashlib.sha256(hash_input.encode()).hexdigest()[:24]}"
    else:
        clean_key = idempotency_key.strip()

    # 3. Prepare normalized request payload (zero credentials or internal paths)
    payload = {
        "source": request.source,
        "canonical_source_id": canonical_source_id,
        "is_url": is_url,
        "start_time": request.start_time,
        "end_time": request.end_time,
        "mode": request.mode,
        "output_format": request.output_format,
        "resolution": request.resolution,
        "video_bitrate": request.video_bitrate,
        "audio_bitrate": request.audio_bitrate,
        "auth_method": user.auth_method.value if hasattr(user.auth_method, "value") else str(user.auth_method),
    }

    # 4. Check for existing idempotency key within tenant
    stmt = select(MediaWorkItemDB).where(
        MediaWorkItemDB.tenant_id == user.tenant_id,
        MediaWorkItemDB.idempotency_key == clean_key,
    )
    res = await db.execute(stmt)
    existing = await safe_scalar_one_or_none(res)
    critical_clip_fields = ["source", "start_time", "end_time", "mode", "output_format", "resolution", "video_bitrate", "audio_bitrate"]
    if existing:
        ex_payload = existing.request_payload or {}
        for fld in critical_clip_fields:
            if ex_payload.get(fld) != payload.get(fld):
                raise HTTPException(
                    status_code=409,
                    detail=f"Idempotency conflict: key '{clean_key}' already used with different parameters (mismatch on '{fld}')",
                )
        return existing

    # Commercial quota admission and reservation (STEP 04-A2)
    from api.services.enforcement_service import acquire_admission_and_reservation
    reservation, is_new_res = await acquire_admission_and_reservation(
        db,
        tenant_id=user.tenant_id,
        operation_type="clip_extract",
        idempotency_key=clean_key,
        payload=payload,
        user_id=user.user_id,
        auth_method=user.auth_method.value if hasattr(user.auth_method, "value") else str(user.auth_method),
    )

    job_id = str(uuid.uuid4())
    reservation.resource_id = job_id
    now = _utcnow()
    item = MediaWorkItemDB(
        id=job_id,
        tenant_id=user.tenant_id,
        requester_user_id=user.user_id,
        requester_api_key_id=user.api_key_id,
        operation_type="clip_extract",
        idempotency_key=clean_key,
        request_payload=payload,
        status="queued",
        attempt_count=0,
        max_attempts=3,
        available_at=now,
        progress_percent=0.0,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    try:
        await db.commit()
        await db.refresh(item)
    except IntegrityError:
        await db.rollback()
        res = await db.execute(stmt)
        existing = await safe_scalar_one_or_none(res)
        if existing:
            ex_payload = existing.request_payload or {}
            for fld in critical_clip_fields:
                if ex_payload.get(fld) != payload.get(fld):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Idempotency conflict: key '{clean_key}' already used with different parameters",
                    )
            return existing
        raise

    await notify_new_job_event(job_id)
    return item


async def submit_merge_job(
    db: AsyncSession,
    user: CurrentUser,
    clips: list[str],
    output_format: str = "mp4",
    mode: str = "auto",
    idempotency_key: Optional[str] = None,
) -> MediaWorkItemDB:
    """Validate merge parameters and submit durable background work item."""
    if not clips or len(clips) < 2:
        raise HTTPException(status_code=400, detail="At least two clips are required to merge")
    if len(clips) > 50:
        raise HTTPException(status_code=400, detail="Cannot merge more than 50 clips in one request")

    # Authorize and resolve all input clips
    resolved_clips = []
    for clip_ref in clips:
        res_ref = await resolve_source_media_reference(db, clip_ref, user.tenant_id, user)
        await verify_source_media_authorization(db, res_ref, user)
        resolve_and_authorize_clip_source(
            source=res_ref,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            is_admin=user.is_admin_or_owner,
        )
        resolved_clips.append(res_ref)
    clips = resolved_clips

    # Derive idempotency key if omitted
    if not idempotency_key or not idempotency_key.strip():
        clip_str = ",".join(sorted(clips))
        hash_input = f"{user.tenant_id}:merge:{clip_str}:{mode}:{output_format}"
        clean_key = f"merge:{hashlib.sha256(hash_input.encode()).hexdigest()[:24]}"
    else:
        clean_key = idempotency_key.strip()

    payload = {
        "clips": clips,
        "output_format": output_format,
        "mode": mode,
        "auth_method": user.auth_method.value if hasattr(user.auth_method, "value") else str(user.auth_method),
    }

    stmt = select(MediaWorkItemDB).where(
        MediaWorkItemDB.tenant_id == user.tenant_id,
        MediaWorkItemDB.idempotency_key == clean_key,
    )
    res = await db.execute(stmt)
    existing = await safe_scalar_one_or_none(res)
    critical_merge_fields = ["clips", "output_format", "mode"]
    if existing:
        ex_payload = existing.request_payload or {}
        for fld in critical_merge_fields:
            if ex_payload.get(fld) != payload.get(fld):
                raise HTTPException(
                    status_code=409,
                    detail=f"Idempotency conflict: key '{clean_key}' already used with different parameters (mismatch on '{fld}')",
                )
        return existing

    # Commercial quota admission and reservation (STEP 04-A2)
    from api.services.enforcement_service import acquire_admission_and_reservation
    reservation, is_new_res = await acquire_admission_and_reservation(
        db,
        tenant_id=user.tenant_id,
        operation_type="merge",
        idempotency_key=clean_key,
        payload=payload,
        user_id=user.user_id,
        auth_method=user.auth_method.value if hasattr(user.auth_method, "value") else str(user.auth_method),
    )

    job_id = str(uuid.uuid4())
    reservation.resource_id = job_id
    now = _utcnow()
    item = MediaWorkItemDB(
        id=job_id,
        tenant_id=user.tenant_id,
        requester_user_id=user.user_id,
        requester_api_key_id=user.api_key_id,
        operation_type="merge",
        idempotency_key=clean_key,
        request_payload=payload,
        status="queued",
        attempt_count=0,
        max_attempts=3,
        available_at=now,
        progress_percent=0.0,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    try:
        await db.commit()
        await db.refresh(item)
    except IntegrityError:
        await db.rollback()
        res = await db.execute(stmt)
        existing = await safe_scalar_one_or_none(res)
        if existing:
            ex_payload = existing.request_payload or {}
            for fld in critical_merge_fields:
                if ex_payload.get(fld) != payload.get(fld):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Idempotency conflict: key '{clean_key}' already used with different parameters",
                    )
            return existing
        raise

    await notify_new_job_event(job_id)
    return item


# ── Query & Authorization Handlers ──


async def get_authorized_job(
    db: AsyncSession,
    job_id: str,
    user: CurrentUser,
) -> MediaWorkItemDB:
    """Retrieve background job with strict tenant isolation and member scoping."""
    clean_id = (job_id or "").strip()
    if not clean_id or not _SAFE_ID_PATTERN.match(clean_id) or len(clean_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid job identifier")

    stmt = (
        select(MediaWorkItemDB)
        .options(selectinload(MediaWorkItemDB.result_artifact))
        .where(MediaWorkItemDB.id == clean_id)
    )
    res = await db.execute(stmt)
    item = await safe_scalar_one_or_none(res)

    if not item or item.tenant_id != user.tenant_id:
        # Cross-tenant anti-enumeration: always 404
        raise HTTPException(status_code=404, detail="Job not found")

    # Intra-tenant member isolation: members see only their own jobs
    if not user.is_admin_or_owner and item.requester_user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Job not found")

    return item


async def list_authorized_jobs(
    db: AsyncSession,
    user: CurrentUser,
    status: Optional[str] = None,
    operation_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[MediaWorkItemDB], int]:
    """List tenant jobs with member isolation and pagination."""
    query = (
        select(MediaWorkItemDB)
        .options(selectinload(MediaWorkItemDB.result_artifact))
        .where(MediaWorkItemDB.tenant_id == user.tenant_id)
    )

    if not user.is_admin_or_owner:
        query = query.where(MediaWorkItemDB.requester_user_id == user.user_id)

    if status:
        query = query.where(MediaWorkItemDB.status == status.strip().lower())
    if operation_type:
        query = query.where(MediaWorkItemDB.operation_type == operation_type.strip().lower())

    count_stmt = select(func.count()).select_from(query.subquery())
    count_res = await db.execute(count_stmt)
    total = (await safe_scalar_one_or_none(count_res)) or 0

    offset = (max(1, page) - 1) * max(1, min(page_size, 100))
    query = query.order_by(MediaWorkItemDB.created_at.desc()).limit(min(page_size, 100)).offset(offset)
    res = await db.execute(query)
    items = await safe_scalars_all(res)
    return items, total


async def request_job_cancellation(
    db: AsyncSession,
    job_id: str,
    user: CurrentUser,
) -> tuple[MediaWorkItemDB, str]:
    """Request job cancellation. Immediately cancels queued jobs; signals running jobs."""
    item = await get_authorized_job(db, job_id, user)

    now = _utcnow()
    if item.status in ("queued", "retry_wait"):
        item.status = "cancelled"
        item.cancellation_requested_at = now
        item.completed_at = now
        item.updated_at = now

        # Release commercial reservation (STEP 04-A2)
        from api.database.models import CommercialReservationDB
        stmt_res = (
            select(CommercialReservationDB)
            .where(
                CommercialReservationDB.tenant_id == item.tenant_id,
                CommercialReservationDB.idempotency_key == item.idempotency_key,
            )
            .with_for_update()
        )
        res_obj = await safe_scalar_one_or_none(await db.execute(stmt_res))
        if res_obj and res_obj.status in ("reserved", "active"):
            res_obj.status = "released"
            res_obj.released_at = now
            res_obj.updated_at = now

        await db.commit()
        await db.refresh(item)
        return item, "Job was queued and has been cancelled immediately."

    if item.status == "running":
        item.cancellation_requested_at = now
        item.updated_at = now
        await db.commit()
        await db.refresh(item)
        await notify_cancel_job_event(item.id)
        return item, "Job is running. Cancellation requested; worker will terminate subprocess."

    # Already terminal
    return item, f"Job is already in terminal state '{item.status}'."


# ── Worker Scheduling & Fencing Handlers ──


async def claim_next_job(
    db: AsyncSession,
    worker_id: str,
    lease_duration_seconds: int = 30,
) -> Optional[MediaWorkItemDB]:
    """Atomically claim the next queued or retryable job using SELECT FOR UPDATE SKIP LOCKED."""
    now = _utcnow()
    stmt = (
        select(MediaWorkItemDB)
        .where(
            MediaWorkItemDB.status.in_(["queued", "retry_wait"]),
            MediaWorkItemDB.available_at <= now,
        )
        .order_by(MediaWorkItemDB.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    res = await db.execute(stmt)
    item = await safe_scalar_one_or_none(res)
    if not item:
        return None

    # Check if cancelled before execution began
    if item.cancellation_requested_at is not None:
        item.status = "cancelled"
        item.completed_at = now
        item.updated_at = now
        await db.commit()
        return None

    item.status = "running"
    item.claimed_by = worker_id
    item.started_at = item.started_at or now
    item.attempt_count = (item.attempt_count or 0) + 1
    item.last_heartbeat_at = now
    item.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
    item.updated_at = now

    # Transition commercial reservation to active
    from api.database.models import CommercialReservationDB
    stmt_res = (
        select(CommercialReservationDB)
        .where(
            CommercialReservationDB.tenant_id == item.tenant_id,
            CommercialReservationDB.idempotency_key == item.idempotency_key,
        )
        .with_for_update()
    )
    res_obj = await safe_scalar_one_or_none(await db.execute(stmt_res))
    if res_obj and res_obj.status in ("reserved", "active"):
        res_obj.status = "active"
        res_obj.resource_id = item.id
        res_obj.lease_expires_at = item.lease_expires_at
        res_obj.updated_at = now

    await db.commit()
    await db.refresh(item)
    return item


async def heartbeat_and_check_cancellation(
    db: AsyncSession,
    job_id: str,
    worker_id: str,
    lease_duration_seconds: int = 30,
    progress_percent: Optional[float] = None,
) -> tuple[bool, bool]:
    """Worker heartbeat to maintain lease and check for external cancellation.

    Returns:
        (is_valid, is_cancellation_requested):
            - is_valid: True if worker still owns the valid lease.
            - is_cancellation_requested: True if user requested cancellation.
    """
    now = _utcnow()
    stmt = select(MediaWorkItemDB).where(
        MediaWorkItemDB.id == job_id,
        MediaWorkItemDB.claimed_by == worker_id,
        MediaWorkItemDB.status == "running",
    )
    res = await db.execute(stmt)
    item = await safe_scalar_one_or_none(res)
    if not item:
        # Worker has been fenced out! (Lease expired and reclaimed)
        return False, False

    cancel_requested = item.cancellation_requested_at is not None

    item.last_heartbeat_at = now
    item.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
    if progress_percent is not None:
        item.progress_percent = max(0.0, min(100.0, float(progress_percent)))
    item.updated_at = now

    # STEP 04-A2-Fix-A: Worker lease renewal extends linked commercial reservation lease
    from api.database.models import CommercialReservationDB
    stmt_res = (
        select(CommercialReservationDB)
        .where(
            CommercialReservationDB.tenant_id == item.tenant_id,
            CommercialReservationDB.status.in_(["reserved", "active"]),
            (CommercialReservationDB.resource_id == item.id) | (CommercialReservationDB.idempotency_key == item.idempotency_key),
        )
    )
    active_res = (await db.execute(stmt_res)).scalars().first()
    if active_res:
        active_res.lease_expires_at = now + timedelta(minutes=15)
        active_res.updated_at = now

    await db.commit()
    return True, cancel_requested


async def complete_job_and_artifact_atomic(
    db: AsyncSession,
    job_id: str,
    worker_id: str,
    artifact_id: str,
    duration_seconds: float,
    file_size_bytes: int,
    sha256_checksum: str,
    usage_event_kwargs: dict[str, Any],
    expected_attempt: Optional[int] = None,
    staging_path: Optional[Path] = None,
    abs_output: Optional[Path] = None,
) -> bool:
    """Transition work item to succeeded and finalize artifact/usage in a single atomic transaction.

    TASK 1 & TASK 3 Invariants:
    1. Row locks acquired and lease/attempt/cancellation verified first.
    2. Physical file published to authoritative output BEFORE database commit (publish_artifact_file_no_clobber).
       If publication fails or conflicts with an existing file, the transaction rolls back immediately.
    3. Job status='succeeded', artifact status='ready', and UsageEventDB committed together atomically.
    4. Never clobbers an existing winning output or reports a succeeded job with a missing file.
    """
    now = _utcnow()
    stmt = (
        select(MediaWorkItemDB)
        .where(
            MediaWorkItemDB.id == job_id,
            MediaWorkItemDB.claimed_by == worker_id,
            MediaWorkItemDB.status == "running",
        )
        .with_for_update()
    )
    res = await db.execute(stmt)
    item = await safe_scalar_one_or_none(res)
    if not item:
        logger.warning("Fenced worker %s tried to complete job %s (lease lost or state changed)", worker_id, job_id)
        return False

    if expected_attempt is not None and item.attempt_count != expected_attempt:
        logger.warning(
            "Fenced worker %s attempt mismatch on job %s: worker had attempt %d, DB has attempt %d",
            worker_id, job_id, expected_attempt, item.attempt_count,
        )
        return False

    if item.cancellation_requested_at is not None:
        logger.warning("Worker %s tried to complete cancelled job %s", worker_id, job_id)
        item.status = "cancelled"
        item.completed_at = now
        item.updated_at = now
        await db.commit()
        return False

    # 1. Physical file publication BEFORE database state transition
    if staging_path is not None and abs_output is not None:
        try:
            from api.security.media_storage import publish_artifact_file_no_clobber
            publish_artifact_file_no_clobber(
                staging_path=staging_path,
                abs_output=abs_output,
                expected_sha256=sha256_checksum,
                expected_size=file_size_bytes,
            )
        except Exception as pub_exc:
            logger.error(
                "Atomic file publication failed for job %s, artifact %s (%s -> %s): %s",
                job_id, artifact_id, staging_path, abs_output, pub_exc,
            )
            await db.rollback()
            return False

    # 2. Update work item
    item.status = "succeeded"
    item.result_artifact_id = artifact_id
    item.progress_percent = 100.0
    item.completed_at = now
    item.updated_at = now

    # 3. Update artifact in the same transaction
    stmt_art = select(MediaArtifactDB).where(MediaArtifactDB.id == artifact_id).with_for_update()
    res_art = await db.execute(stmt_art)
    art = await safe_scalar_one_or_none(res_art)
    if art:
        art.status = "ready"
        art.duration_seconds = max(duration_seconds, 0.0)
        art.file_size_bytes = file_size_bytes
        art.sha256_checksum = sha256_checksum
        art.updated_at = now

    # 4. Insert usage event in the same transaction
    from api.database.models import UsageEventDB

    usage_event = UsageEventDB(
        id=str(uuid.uuid4()),
        tenant_id=usage_event_kwargs["tenant_id"],
        event_type=usage_event_kwargs["event_type"],
        idempotency_key=usage_event_kwargs["idempotency_key"],
        resource_id=usage_event_kwargs["resource_id"],
        quantity=float(usage_event_kwargs["quantity"]),
        unit=usage_event_kwargs["unit"],
        bytes=int(usage_event_kwargs["bytes"]),
        duration_seconds=float(usage_event_kwargs["duration_seconds"]),
        user_id=usage_event_kwargs.get("user_id"),
        api_key_id=usage_event_kwargs.get("api_key_id"),
        auth_method=usage_event_kwargs.get("auth_method", "worker"),
        metadata_json=usage_event_kwargs.get("metadata_json", {}),
        created_at=now,
    )
    db.add(usage_event)

    # 5. Finalize commercial reservation in the same transaction (STEP 04-A2)
    from api.database.models import CommercialReservationDB
    stmt_res = (
        select(CommercialReservationDB)
        .where(
            CommercialReservationDB.tenant_id == item.tenant_id,
            CommercialReservationDB.idempotency_key == item.idempotency_key,
        )
        .with_for_update()
    )
    res_obj = await safe_scalar_one_or_none(await db.execute(stmt_res))
    if res_obj and res_obj.status in ("reserved", "active"):
        res_obj.status = "finalized"
        res_obj.resource_id = job_id
        res_obj.finalized_at = now
        res_obj.updated_at = now

    try:
        await db.commit()
        return True
    except IntegrityError as exc:
        await db.rollback()
        logger.error("Integrity error completing job %s: %s", job_id, exc)
        return False
    except Exception as exc:
        await db.rollback()
        logger.error("Database commit error completing job %s: %s", job_id, exc)
        return False


async def complete_job_succeeded(
    db: AsyncSession,
    job_id: str,
    worker_id: str,
    result_artifact_id: str,
) -> bool:
    """Transition work item to succeeded with stale-worker fencing."""
    now = _utcnow()
    stmt = select(MediaWorkItemDB).where(
        MediaWorkItemDB.id == job_id,
        MediaWorkItemDB.claimed_by == worker_id,
        MediaWorkItemDB.status == "running",
    )
    res = await db.execute(stmt)
    item = await safe_scalar_one_or_none(res)
    if not item:
        logger.warning("Fenced worker %s tried to complete job %s", worker_id, job_id)
        return False

    item.status = "succeeded"
    item.result_artifact_id = result_artifact_id
    item.progress_percent = 100.0
    item.completed_at = now
    item.updated_at = now
    await db.commit()
    return True


async def mark_job_cancelled(
    db: AsyncSession,
    job_id: str,
    worker_id: str,
) -> None:
    """Transition job to cancelled status upon worker abort."""
    now = _utcnow()
    stmt = select(MediaWorkItemDB).where(
        MediaWorkItemDB.id == job_id,
        MediaWorkItemDB.claimed_by == worker_id,
    )
    res = await db.execute(stmt)
    item = await safe_scalar_one_or_none(res)
    if item:
        item.status = "cancelled"
        item.completed_at = now
        item.updated_at = now

        from api.database.models import CommercialReservationDB
        stmt_res = (
            select(CommercialReservationDB)
            .where(
                CommercialReservationDB.tenant_id == item.tenant_id,
                CommercialReservationDB.idempotency_key == item.idempotency_key,
            )
            .with_for_update()
        )
        res_obj = await safe_scalar_one_or_none(await db.execute(stmt_res))
        if res_obj and res_obj.status in ("reserved", "active"):
            res_obj.status = "released"
            res_obj.released_at = now
            res_obj.updated_at = now

        await db.commit()


async def fail_job(
    db: AsyncSession,
    job_id: str,
    worker_id: str,
    error_code: str,
    error_message: str,
    retryable: bool = False,
    backoff_seconds: int = 5,
) -> None:
    """Record job failure with retry classification and exponential backoff."""
    now = _utcnow()
    stmt = select(MediaWorkItemDB).where(
        MediaWorkItemDB.id == job_id,
        MediaWorkItemDB.claimed_by == worker_id,
    )
    res = await db.execute(stmt)
    item = await safe_scalar_one_or_none(res)
    if not item:
        return

    clean_error = sanitize_media_error(error_message)
    item.sanitized_error_code = error_code[:64]
    item.sanitized_error_message = clean_error

    if retryable and item.attempt_count < item.max_attempts and item.cancellation_requested_at is None:
        item.status = "retry_wait"
        item.available_at = now + timedelta(seconds=backoff_seconds * item.attempt_count)
        item.claimed_by = None
        item.lease_expires_at = None
        item.updated_at = now
        logger.info(
            "Job %s marked retry_wait (attempt %d/%d, next available at %s): %s",
            job_id,
            item.attempt_count,
            item.max_attempts,
            item.available_at,
            clean_error,
        )
    else:
        item.status = "failed"
        item.completed_at = now
        item.updated_at = now
        logger.warning(
            "Job %s marked failed (attempt %d/%d): %s",
            job_id,
            item.attempt_count,
            item.max_attempts,
            clean_error,
        )

        # Release commercial reservation on permanent failure
        from api.database.models import CommercialReservationDB
        stmt_res = (
            select(CommercialReservationDB)
            .where(
                CommercialReservationDB.tenant_id == item.tenant_id,
                CommercialReservationDB.idempotency_key == item.idempotency_key,
            )
            .with_for_update()
        )
        res_obj = await safe_scalar_one_or_none(await db.execute(stmt_res))
        if res_obj and res_obj.status in ("reserved", "active"):
            res_obj.status = "released"
            res_obj.released_at = now
            res_obj.updated_at = now

    await db.commit()


async def recover_expired_leases(db: AsyncSession) -> int:
    """Recover jobs whose workers crashed or failed to heartbeat.

    Expired running jobs are either requeued for retry (if attempt_count < max_attempts)
    or marked failed. If cancellation was requested before lease expired, job is marked cancelled.
    """
    now = _utcnow()
    stmt = (
        select(MediaWorkItemDB)
        .where(
            MediaWorkItemDB.status == "running",
            MediaWorkItemDB.lease_expires_at < now,
        )
        .with_for_update(skip_locked=True)
    )
    res = await db.execute(stmt)
    expired_items = await safe_scalars_all(res)
    count = 0

    for item in expired_items:
        count += 1
        if item.cancellation_requested_at is not None:
            item.status = "cancelled"
            item.completed_at = now
            item.updated_at = now
            logger.info("Recovered expired job %s into cancelled state (cancellation requested)", item.id)
        elif item.attempt_count < item.max_attempts:
            item.status = "retry_wait"
            # Exponential backoff: min(300, 5 * 2^attempts) to prevent retry storms
            backoff_sec = min(300, 5 * (2 ** int(item.attempt_count or 0)))
            item.available_at = now + timedelta(seconds=backoff_sec)
            item.sanitized_error_code = "LEASE_EXPIRED"
            item.sanitized_error_message = f"Worker lease expired (last heartbeat: {item.last_heartbeat_at})"
            item.claimed_by = None
            item.lease_expires_at = None
            item.updated_at = now
            logger.info("Recovered expired job %s into retry_wait (backoff=%ds)", item.id, backoff_sec)
        else:
            item.status = "failed"
            item.sanitized_error_code = "LEASE_TIMEOUT_EXCEEDED"
            item.sanitized_error_message = "Worker process terminated and maximum retry attempts exceeded"
            item.completed_at = now
            item.updated_at = now
            logger.warning("Expired job %s exceeded attempts; marked failed", item.id)

    if count > 0:
        await db.commit()
    return count
