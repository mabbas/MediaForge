"""Commercial API usage metering, ledger tracking and advisory quota evaluation (STEP 02-E2B)."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session_factory
from api.database.models import APIKeyDB, TenantDB, TenantQuotaDB, UsageEventDB, UsageTrackingDB

logger = logging.getLogger(__name__)

# Optional override hook for test fixtures
async_session_factory = None


class IdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused with conflicting operation parameters."""

    pass


def _validate_idempotency_consistency(
    existing: UsageEventDB,
    *,
    event_type: str,
    resource_id: str | None,
    quantity: float,
    bytes_val: int,
    duration_seconds: float,
) -> None:
    """Ensure duplicate idempotency submission does not alter authoritative accounting quantities."""
    conflicts: list[str] = []
    if existing.event_type != event_type:
        conflicts.append(f"event_type ('{existing.event_type}' vs '{event_type}')")
    if resource_id and existing.resource_id and existing.resource_id != resource_id:
        conflicts.append(f"resource_id ('{existing.resource_id}' vs '{resource_id}')")
    if abs(float(existing.quantity or 0) - float(quantity)) > 1e-4:
        conflicts.append(f"quantity ({existing.quantity} vs {quantity})")
    if int(existing.bytes or 0) != int(bytes_val):
        conflicts.append(f"bytes ({existing.bytes} vs {bytes_val})")
    if abs(float(existing.duration_seconds or 0) - float(duration_seconds)) > 1e-3:
        conflicts.append(f"duration_seconds ({existing.duration_seconds} vs {duration_seconds})")

    if conflicts:
        raise IdempotencyConflictError(
            f"Idempotency conflict for key '{existing.idempotency_key}': incoming parameters "
            f"do not match existing authoritative event: {', '.join(conflicts)}"
        )


# Standard metered metrics catalog
METERED_METRICS = [
    ("downloads", "count", "download"),
    ("download_bytes", "bytes", "download"),
    ("clips", "count", "clip"),
    ("clip_seconds", "seconds", "clip"),
    ("merges", "count", "merge"),
    ("transcription_minutes", "minutes", "transcription"),
]


async def record_usage_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    event_type: str,
    idempotency_key: str,
    resource_id: str | None = None,
    quantity: float = 1.0,
    unit: str = "count",
    bytes: int = 0,
    duration_seconds: float = 0.0,
    user_id: str | None = None,
    api_key_id: str | None = None,
    auth_method: str = "jwt",
    client_ip: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> tuple[UsageEventDB, bool]:
    """Record a persistent metered usage event with database-enforced idempotency.

    Returns:
        (event, is_new): A tuple containing the usage event and a boolean indicating
                         whether this was a new insertion (True) or a deduplicated replay (False).
    """
    clean_key = str(idempotency_key).strip()
    if not clean_key:
        raise ValueError("idempotency_key cannot be empty")

    # 1. Check for existing event with the same tenant and idempotency key
    stmt = select(UsageEventDB).where(
        UsageEventDB.tenant_id == tenant_id,
        UsageEventDB.idempotency_key == clean_key,
    )
    res = await session.execute(stmt)
    existing = res.scalars().first()
    if existing:
        _validate_idempotency_consistency(
            existing,
            event_type=event_type,
            resource_id=resource_id,
            quantity=float(quantity),
            bytes_val=int(bytes),
            duration_seconds=float(duration_seconds),
        )
        logger.debug(
            "Idempotent event duplicate verified for tenant %s key %s",
            tenant_id,
            clean_key,
        )
        return existing, False

    # 2. Resolve safe user_id for database foreign key constraint
    # Service account callers (e.g. "service:<key_id>") do not have rows in gid.users.
    # Attribute to the key's owning user if present, or set to None.
    effective_user_id = user_id
    if effective_user_id and effective_user_id.startswith("service:"):
        effective_user_id = None
        if api_key_id:
            try:
                key_rec = await session.get(APIKeyDB, api_key_id)
                if key_rec and key_rec.user_id:
                    effective_user_id = key_rec.user_id
            except Exception:
                effective_user_id = None

    # 3. Insert new event record
    event = UsageEventDB(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=effective_user_id,
        api_key_id=api_key_id,
        event_type=event_type,
        idempotency_key=clean_key,
        resource_id=resource_id,
        quantity=float(quantity),
        unit=unit,
        bytes=int(bytes),
        duration_seconds=float(duration_seconds),
        auth_method=auth_method,
        client_ip=client_ip,
        metadata_json=metadata_json or {},
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(event)

    try:
        await session.flush()
    except IntegrityError:
        # Race condition: concurrent worker inserted same idempotency key
        await session.rollback()
        res = await session.execute(stmt)
        existing = res.scalars().first()
        if existing:
            _validate_idempotency_consistency(
                existing,
                event_type=event_type,
                resource_id=resource_id,
                quantity=float(quantity),
                bytes_val=int(bytes),
                duration_seconds=float(duration_seconds),
            )
            return existing, False
        raise

    # 3. Maintain daily aggregate in usage_tracking for backwards compatibility
    if user_id:
        try:
            today_str = date.today().isoformat()
            ut_stmt = select(UsageTrackingDB).where(
                UsageTrackingDB.user_id == user_id,
                UsageTrackingDB.tenant_id == tenant_id,
                UsageTrackingDB.date == today_str,
            )
            ut_res = await session.execute(ut_stmt)
            ut = ut_res.scalars().first()
            if not ut:
                ut = UsageTrackingDB(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    tenant_id=tenant_id,
                    date=today_str,
                    downloads_count=0,
                    bytes_downloaded=0,
                    transcripts_count=0,
                    whisper_minutes=0.0,
                    api_calls_count=0,
                )
                session.add(ut)

            if event_type == "download":
                ut.downloads_count = (ut.downloads_count or 0) + 1
                ut.bytes_downloaded = (ut.bytes_downloaded or 0) + int(bytes)
            elif event_type == "transcription":
                ut.transcripts_count = (ut.transcripts_count or 0) + 1
                ut.whisper_minutes = (ut.whisper_minutes or 0.0) + (float(duration_seconds) / 60.0)
            ut.api_calls_count = (ut.api_calls_count or 0) + 1
        except Exception as exc:
            logger.warning("Could not update legacy usage_tracking: %s", exc)

    return event, True


async def record_usage_in_fresh_session(**kwargs) -> tuple[str, bool]:
    """Helper to record a usage event in a fresh decoupled session."""
    factory = async_session_factory or get_session_factory()
    if factory is None:
        logger.error("Database async session factory unavailable for usage recording")
        return "", False

    async with factory() as session:
        try:
            event, is_new = await record_usage_event(session, **kwargs)
            await session.commit()
            return event.id, is_new
        except Exception as exc:
            await session.rollback()
            logger.error("Failed to record decoupled usage event: %s", exc)
            return "", False


# ── Specialized Helpers for Operations ──


async def record_download_usage(
    *,
    tenant_id: str,
    job_id: str,
    file_size_bytes: int = 0,
    duration_seconds: float = 0.0,
    user_id: str | None = None,
    api_key_id: str | None = None,
    auth_method: str = "jwt",
    client_ip: str | None = None,
    url: str | None = None,
    format: str | None = None,
    quality: str | None = None,
) -> tuple[str, bool]:
    """Record consumption for completed media download."""
    idempotency_key = f"download:{job_id}"
    metadata = {
        "url": url,
        "format": format,
        "quality": quality,
    }
    return await record_usage_in_fresh_session(
        tenant_id=tenant_id,
        user_id=user_id,
        api_key_id=api_key_id,
        event_type="download",
        idempotency_key=idempotency_key,
        resource_id=job_id,
        quantity=1.0,
        unit="count",
        bytes=file_size_bytes,
        duration_seconds=duration_seconds,
        auth_method=auth_method,
        client_ip=client_ip,
        metadata_json=metadata,
    )


async def record_clip_usage(
    *,
    tenant_id: str,
    artifact_id: str,
    file_size_bytes: int = 0,
    duration_seconds: float = 0.0,
    user_id: str | None = None,
    api_key_id: str | None = None,
    auth_method: str = "jwt",
    client_ip: str | None = None,
    source: str | None = None,
    mode: str | None = None,
) -> tuple[str, bool]:
    """Record consumption for extracted clip."""
    idempotency_key = f"clip:{artifact_id}"
    metadata = {
        "source": source,
        "mode": mode,
    }
    return await record_usage_in_fresh_session(
        tenant_id=tenant_id,
        user_id=user_id,
        api_key_id=api_key_id,
        event_type="clip",
        idempotency_key=idempotency_key,
        resource_id=artifact_id,
        quantity=1.0,
        unit="count",
        bytes=file_size_bytes,
        duration_seconds=duration_seconds,
        auth_method=auth_method,
        client_ip=client_ip,
        metadata_json=metadata,
    )


async def record_merge_usage(
    *,
    tenant_id: str,
    artifact_id: str,
    file_size_bytes: int = 0,
    duration_seconds: float = 0.0,
    clip_count: int = 0,
    user_id: str | None = None,
    api_key_id: str | None = None,
    auth_method: str = "jwt",
    client_ip: str | None = None,
    clips_used: list[str] | None = None,
) -> tuple[str, bool]:
    """Record consumption for merged video."""
    idempotency_key = f"merge:{artifact_id}"
    metadata = {
        "clip_count": clip_count,
        "clips_used": clips_used or [],
    }
    return await record_usage_in_fresh_session(
        tenant_id=tenant_id,
        user_id=user_id,
        api_key_id=api_key_id,
        event_type="merge",
        idempotency_key=idempotency_key,
        resource_id=artifact_id,
        quantity=1.0,
        unit="count",
        bytes=file_size_bytes,
        duration_seconds=duration_seconds,
        auth_method=auth_method,
        client_ip=client_ip,
        metadata_json=metadata,
    )


async def record_transcription_usage(
    *,
    tenant_id: str,
    transcript_id: str,
    duration_seconds: float = 0.0,
    word_count: int = 0,
    user_id: str | None = None,
    api_key_id: str | None = None,
    auth_method: str = "jwt",
    client_ip: str | None = None,
    language: str | None = None,
    format: str | None = None,
) -> tuple[str, bool]:
    """Record consumption for AI speech-to-text transcription (e.g. Whisper)."""
    idempotency_key = f"transcription:{transcript_id}"
    minutes = round(duration_seconds / 60.0, 2) if duration_seconds > 0 else 0.0
    metadata = {
        "word_count": word_count,
        "language": language,
        "format": format,
        "engine": "whisper_ai",
        "is_ai_speech_to_text": True,
    }
    return await record_usage_in_fresh_session(
        tenant_id=tenant_id,
        user_id=user_id,
        api_key_id=api_key_id,
        event_type="transcription",
        idempotency_key=idempotency_key,
        resource_id=transcript_id,
        quantity=minutes,
        unit="minutes",
        bytes=0,
        duration_seconds=duration_seconds,
        auth_method=auth_method,
        client_ip=client_ip,
        metadata_json=metadata,
    )


async def record_subtitle_usage(
    *,
    tenant_id: str,
    transcript_id: str,
    word_count: int = 0,
    bytes_count: int = 0,
    user_id: str | None = None,
    api_key_id: str | None = None,
    auth_method: str = "jwt",
    client_ip: str | None = None,
    language: str | None = None,
    format: str | None = None,
) -> tuple[str, bool]:
    """Record consumption for subtitle/caption extraction (non-AI download)."""
    idempotency_key = f"subtitle:{transcript_id}"
    metadata = {
        "word_count": word_count,
        "language": language,
        "format": format,
        "engine": "subtitle_download",
        "is_ai_speech_to_text": False,
    }
    return await record_usage_in_fresh_session(
        tenant_id=tenant_id,
        user_id=user_id,
        api_key_id=api_key_id,
        event_type="subtitle_retrieval",
        idempotency_key=idempotency_key,
        resource_id=transcript_id,
        quantity=1.0,
        unit="count",
        bytes=bytes_count,
        duration_seconds=0.0,
        auth_method=auth_method,
        client_ip=client_ip,
        metadata_json=metadata,
    )


# ── Reporting and Query Services ──


async def get_tenant_usage_summary(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    period: str = "all_time",
) -> dict[str, Any]:
    """Compute aggregated usage metrics for a tenant across downloads, clips, merges and transcripts."""
    # Build base filter
    filters = [UsageEventDB.tenant_id == tenant_id]
    if user_id:
        filters.append(UsageEventDB.user_id == user_id)
    if start_date:
        filters.append(UsageEventDB.created_at >= start_date)
    if end_date:
        filters.append(UsageEventDB.created_at <= end_date)

    # 1. Total counts and bytes
    totals_stmt = select(
        func.count(UsageEventDB.id).label("total_events"),
        func.coalesce(func.sum(UsageEventDB.bytes), 0).label("total_bytes"),
    ).where(*filters)
    totals_res = (await session.execute(totals_stmt)).first()

    total_events = totals_res.total_events if totals_res else 0
    total_bytes = totals_res.total_bytes if totals_res else 0

    # 2. Breakdown by event_type
    type_stmt = select(
        UsageEventDB.event_type,
        func.count(UsageEventDB.id).label("count"),
        func.coalesce(func.sum(UsageEventDB.bytes), 0).label("bytes"),
        func.coalesce(func.sum(UsageEventDB.duration_seconds), 0.0).label("duration_seconds"),
        func.coalesce(func.sum(UsageEventDB.quantity), 0.0).label("total_quantity"),
    ).where(*filters).group_by(UsageEventDB.event_type)

    type_rows = (await session.execute(type_stmt)).all()
    breakdown_by_type: dict[str, dict[str, Any]] = {}
    for r in type_rows:
        breakdown_by_type[r.event_type] = {
            "count": r.count,
            "bytes": r.bytes,
            "duration_seconds": round(r.duration_seconds, 2),
            "quantity": round(r.total_quantity, 2),
        }

    # 3. Breakdown by user
    user_stmt = select(
        UsageEventDB.user_id,
        func.count(UsageEventDB.id).label("count"),
        func.coalesce(func.sum(UsageEventDB.bytes), 0).label("bytes"),
    ).where(*filters).group_by(UsageEventDB.user_id)
    user_rows = (await session.execute(user_stmt)).all()
    breakdown_by_user = {
        (r.user_id or "anonymous"): {"count": r.count, "bytes": r.bytes}
        for r in user_rows
    }

    # 4. Breakdown by API key
    key_stmt = select(
        UsageEventDB.api_key_id,
        func.count(UsageEventDB.id).label("count"),
        func.coalesce(func.sum(UsageEventDB.bytes), 0).label("bytes"),
    ).where(*filters).group_by(UsageEventDB.api_key_id)
    key_rows = (await session.execute(key_stmt)).all()
    breakdown_by_api_key = {
        (r.api_key_id or "session"): {"count": r.count, "bytes": r.bytes}
        for r in key_rows
    }

    # 5. Total processing time (server compute elapsed ms)
    time_stmt = select(UsageEventDB.metadata_json).where(*filters)
    time_res = await session.execute(time_stmt)
    total_processing_time_ms = 0
    for (m_json,) in time_res.all():
        if isinstance(m_json, dict):
            pt = m_json.get("processing_time_ms")
            if isinstance(pt, (int, float)):
                total_processing_time_ms += int(pt)

    # Prepare structured totals
    dl = breakdown_by_type.get("download", {})
    cl = breakdown_by_type.get("clip", {})
    mg = breakdown_by_type.get("merge", {})
    tr = breakdown_by_type.get("transcription", {})
    sub = breakdown_by_type.get("subtitle_retrieval", {})

    totals = {
        "total_events": total_events,
        "total_bytes": total_bytes,
        "total_processing_time_ms": total_processing_time_ms,
        "downloads_count": dl.get("count", 0),
        "download_bytes": dl.get("bytes", 0),
        "clips_count": cl.get("count", 0),
        "clip_seconds": cl.get("duration_seconds", 0.0),
        "clip_bytes": cl.get("bytes", 0),
        "merges_count": mg.get("count", 0),
        "merge_seconds": mg.get("duration_seconds", 0.0),
        "merge_bytes": mg.get("bytes", 0),
        "subtitles_count": sub.get("count", 0),
        "transcriptions_count": tr.get("count", 0),
        "transcription_minutes": round(tr.get("duration_seconds", 0.0) / 60.0, 2),
    }

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "period": period,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "totals": totals,
        "breakdown_by_type": breakdown_by_type,
        "breakdown_by_user": breakdown_by_user,
        "breakdown_by_api_key": breakdown_by_api_key,
    }


async def get_tenant_usage_events(
    session: AsyncSession,
    *,
    tenant_id: str,
    event_type: str | None = None,
    user_id: str | None = None,
    api_key_id: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[UsageEventDB], int]:
    """Retrieve paginated usage events ledger with multi-tenant filtering."""
    filters = [UsageEventDB.tenant_id == tenant_id]
    if event_type:
        filters.append(UsageEventDB.event_type == event_type)
    if user_id:
        filters.append(UsageEventDB.user_id == user_id)
    if api_key_id:
        filters.append(UsageEventDB.api_key_id == api_key_id)
    if start_date:
        filters.append(UsageEventDB.created_at >= start_date)
    if end_date:
        filters.append(UsageEventDB.created_at <= end_date)

    count_stmt = select(func.count(UsageEventDB.id)).where(*filters)
    total = (await session.execute(count_stmt)).scalar() or 0

    offset = (max(1, page) - 1) * max(1, min(page_size, 100))
    stmt = (
        select(UsageEventDB)
        .where(*filters)
        .order_by(UsageEventDB.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    events = (await session.execute(stmt)).scalars().all()
    return list(events), total


async def get_tenant_advisory_quotas(
    session: AsyncSession,
    *,
    tenant_id: str,
    period: str = "monthly",
) -> dict[str, Any]:
    """Evaluate advisory quotas for tenant with default UNLIMITED (-1) policy."""
    # Determine period start
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if period == "daily":
        start_date = datetime(now.year, now.month, now.day)
    else:  # monthly default
        start_date = datetime(now.year, now.month, 1)

    # 1. Fetch current tenant and tier
    tenant = await session.get(TenantDB, tenant_id)
    tier = tenant.tier if tenant else "basic"

    # 2. Fetch any explicit quota configurations
    q_stmt = select(TenantQuotaDB).where(
        TenantQuotaDB.tenant_id == tenant_id,
        TenantQuotaDB.period == period,
    )
    quota_rows = (await session.execute(q_stmt)).scalars().all()
    configured_quotas = {q.metric: q for q in quota_rows}

    # 3. Calculate current period usage
    summary = await get_tenant_usage_summary(
        session,
        tenant_id=tenant_id,
        start_date=start_date,
        end_date=now,
        period=period,
    )
    totals = summary.get("totals", {})

    quotas_result: dict[str, dict[str, Any]] = {}
    metric_map = {
        "downloads": ("count", totals.get("downloads_count", 0)),
        "download_bytes": ("bytes", totals.get("download_bytes", 0)),
        "clips": ("count", totals.get("clips_count", 0)),
        "clip_seconds": ("seconds", totals.get("clip_seconds", 0.0)),
        "merges": ("count", totals.get("merges_count", 0)),
        "transcription_minutes": ("minutes", totals.get("transcription_minutes", 0.0)),
    }

    for metric_name, (unit, used_val) in metric_map.items():
        q_obj = configured_quotas.get(metric_name)
        limit_val = q_obj.limit_value if q_obj else -1  # Default UNLIMITED (-1)
        is_hard = q_obj.is_hard_limit if q_obj else False

        remaining = -1
        is_exceeded = False
        if limit_val != -1:
            remaining = max(0, int(limit_val - used_val))
            is_exceeded = used_val >= limit_val

        quotas_result[metric_name] = {
            "metric": metric_name,
            "period": period,
            "used": used_val,
            "limit": limit_val,
            "remaining": remaining,
            "unit": unit,
            "is_exceeded": is_exceeded,
            "is_hard_limit": is_hard,
        }

    return {
        "tenant_id": tenant_id,
        "tier": tier,
        "period": period,
        "quotas": quotas_result,
    }
