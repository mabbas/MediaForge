"""Clip extraction API endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_api_settings
from api.database.connection import get_session
from api.database.models import MediaArtifactDB
from api.dependencies import CurrentUser, get_current_user, require_scope
from api.schemas.clips import (
    ClipExtractRequest,
    ClipResultResponse,
    ClipValidateRequest,
    ClipValidateResponse,
)
from api.services.artifact_service import (
    complete_artifact_in_fresh_session,
    compute_sha256,
    create_artifact_record,
    fail_artifact_in_fresh_session,
    resolve_source_media_reference,
    safe_scalar_first,
    verify_source_media_authorization,
)
from api.security.media_storage import (
    MIN_FREE_DISK_SPACE_MB,
    _SAFE_ID_PATTERN,
    generate_safe_output_path,
    get_ffmpeg_semaphore,
    get_tenant_media_dir,
    get_tenant_storage_root,
    resolve_and_authorize_clip_source,
    validate_safe_filename,
)
from api.security.url_policy import validate_url
from src.clip.extractor import (
    ClipExtractor,
    ClipRequest,
    get_clip_extractor,
    reset_clip_extractor,
)
from src.exceptions import FFmpegNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/clips",
    tags=["clips"],
)


def _ensure_ffmpeg_env_from_config() -> None:
    """Ensure GID_FFMPEG_LOCATION is in os.environ from API config (e.g. when run via desktop)."""
    if os.environ.get("GID_FFMPEG_LOCATION", "").strip():
        return
    try:
        settings = get_api_settings()
        if getattr(settings, "ffmpeg_location", None):
            os.environ["GID_FFMPEG_LOCATION"] = settings.ffmpeg_location
            reset_clip_extractor()
    except Exception:
        pass


@router.post(
    "/extract",
    response_model=ClipResultResponse,
    summary="Extract a clip from video",
    description="Extract a clip from a local file or URL using start/end timestamps. Supports fast (keyframe) and precise (re-encode) modes.",
)
async def extract_clip(
    request: ClipExtractRequest,
    user: CurrentUser = Depends(require_scope("clips:write")),
    db: AsyncSession = Depends(get_session),
):
    """Extract a clip from a video with tenant isolation and path protection."""
    # 1. Validate source URL or resolve tenant-isolated local file
    is_url = request.source.startswith("http://") or request.source.startswith("https://")

    if is_url:
        validate_url(request.source)
        source_target = request.source
        src_name = "web"
        source_duration = None
    elif "://" in request.source:
        raise HTTPException(
            status_code=400,
            detail="Unsupported URL scheme in clip source. Only http and https are permitted",
        )
    else:
        # Check source ownership in database and resolve identifier/path
        resolved_source = await resolve_source_media_reference(db, request.source, user.tenant_id, user)
        await verify_source_media_authorization(db, resolved_source, user)
        # Offline clipping: resolve and authorize local file strictly within caller's tenant
        source_path = resolve_and_authorize_clip_source(
            source=resolved_source,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            is_admin=user.is_admin_or_owner,
        )
        source_target = str(source_path)
        src_name = source_path.stem

    # 2. Check disk space before launching heavy process
    try:
        free_mb = shutil.disk_usage(get_tenant_storage_root(user.tenant_id)).free / (1024 * 1024)
        if free_mb < MIN_FREE_DISK_SPACE_MB:
            raise HTTPException(
                status_code=507,
                detail=f"Insufficient disk space ({free_mb:.0f} MB available, minimum {MIN_FREE_DISK_SPACE_MB} MB required)",
            )
    except OSError:
        pass

    # 3. Validate timestamps syntax and bounds first
    valid, error = ClipExtractor.validate_timestamps(
        request.start_time, request.end_time
    )
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    # 4. Ensure FFmpeg is available
    _ensure_ffmpeg_env_from_config()
    try:
        extractor = get_clip_extractor()
    except FFmpegNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    # Probe duration if source is local
    if not is_url and os.path.exists(source_target):
        source_duration = extractor.get_video_duration(source_target)
        valid, error = ClipExtractor.validate_timestamps(
            request.start_time, request.end_time, source_duration=source_duration
        )
        if not valid:
            raise HTTPException(status_code=400, detail=error)

    # 5. Generate safe collision-resistant output path within tenant clips folder
    safe_start = request.start_time.replace(":", "-").replace(".", "-")
    safe_end = request.end_time.replace(":", "-").replace(".", "-")
    prefix = f"{src_name}_clip_{safe_start}_to_{safe_end}"

    abs_output_path, rel_output_path, cid = generate_safe_output_path(
        tenant_id=user.tenant_id,
        subfolder="clips",
        prefix=prefix,
        extension=request.output_format,
    )

    clip_req = ClipRequest(
        source=source_target,
        start_time=request.start_time,
        end_time=request.end_time,
        output_path=str(abs_output_path),
        output_format=request.output_format,
        mode=request.mode,
        resolution=request.resolution,
        video_bitrate=request.video_bitrate,
        audio_bitrate=request.audio_bitrate,
        clip_id=cid,
        is_url=is_url,
    )

    # 6. Decoupled FFmpeg execution: persist pending artifact record before FFmpeg
    try:
        await create_artifact_record(
            db=db,
            artifact_id=cid,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            api_key_id=user.api_key_id,
            artifact_type="clip",
            storage_path=rel_output_path,
            filename=abs_output_path.name,
            format=request.output_format,
            status="processing",
            input_sources=[request.source],
            metadata_json={
                "mode": request.mode,
                "start_time": request.start_time,
                "end_time": request.end_time,
                "resolution": request.resolution,
                "video_bitrate": request.video_bitrate,
                "audio_bitrate": request.audio_bitrate,
            },
        )
    except Exception as exc:
        logger.warning("Could not persist initial artifact record %s: %s", cid, exc)

    # 7. Execute with concurrency bounding
    sem = get_ffmpeg_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=10.0)
    except asyncio.TimeoutError:
        await fail_artifact_in_fresh_session(cid, "Media processing concurrency limit reached")
        raise HTTPException(
            status_code=503,
            detail="Media processing concurrency limit reached. Please retry shortly.",
        )

    try:
        t0 = time.perf_counter()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, extractor.extract, clip_req)
        processing_time_ms = max(0, int((time.perf_counter() - t0) * 1000))
    except Exception as exc:
        await fail_artifact_in_fresh_session(cid, str(exc))
        if abs_output_path.exists():
            try:
                abs_output_path.unlink()
            except OSError:
                pass
        raise HTTPException(
            status_code=500,
            detail="Clip extraction encountered an internal error",
        )
    finally:
        sem.release()

    if not result.success:
        await fail_artifact_in_fresh_session(cid, result.error or "Extraction failed")
        if abs_output_path.exists():
            try:
                abs_output_path.unlink()
            except OSError:
                pass
        raise HTTPException(
            status_code=500,
            detail=result.error or "Extraction failed",
        )

    # 8. Compute sha256 checksum and complete artifact + usage atomically in fresh session
    sha256 = compute_sha256(abs_output_path) if abs_output_path.exists() else None
    usage_kwargs = {
        "tenant_id": user.tenant_id,
        "event_type": "clip",
        "idempotency_key": f"clip:{cid}",
        "resource_id": cid,
        "quantity": 1.0,
        "unit": "count",
        "bytes": result.file_size_bytes or 0,
        "duration_seconds": result.duration_seconds or 0.0,
        "user_id": user.user_id,
        "api_key_id": user.api_key_id,
        "auth_method": user.auth_method.value if hasattr(user.auth_method, "value") else str(user.auth_method),
        "metadata_json": {
            "source": request.source,
            "mode": request.mode,
            "processing_time_ms": processing_time_ms,
        },
    }

    await complete_artifact_in_fresh_session(
        artifact_id=cid,
        duration_seconds=result.duration_seconds,
        file_size_bytes=result.file_size_bytes,
        sha256_checksum=sha256 or "",
        usage_event_kwargs=usage_kwargs,
    )

    # Return safe relative output path, never leaking server-side absolute paths
    return ClipResultResponse(
        success=result.success,
        clip_id=result.clip_id,
        source=request.source if is_url else rel_output_path,
        output_path=rel_output_path,
        start_time=result.start_time,
        end_time=result.end_time,
        duration_seconds=result.duration_seconds,
        file_size_bytes=result.file_size_bytes,
        file_size_human=result.file_size_human,
        created_at=result.created_at,
        error=result.error,
    )


@router.post(
    "/validate",
    response_model=ClipValidateResponse,
    summary="Validate clip timestamps",
    description="Validate start/end timestamps before extracting.",
)
async def validate_timestamps(
    request: ClipValidateRequest,
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """Validate timestamps for clip extraction with authorization check."""
    extractor = get_clip_extractor()

    source_duration = None
    if request.source:
        raw_s = request.source.strip()
        is_url = raw_s.startswith("http://") or raw_s.startswith("https://")
        if not is_url:
            if raw_s.startswith(("/", "\\")) or (len(raw_s) >= 2 and raw_s[1] == ":"):
                raise HTTPException(
                    status_code=400,
                    detail="Absolute filesystem paths are prohibited in API media requests",
                )
            try:
                resolved_source = await resolve_source_media_reference(db, request.source, user.tenant_id, user)
                await verify_source_media_authorization(db, resolved_source, user)
                source_path = resolve_and_authorize_clip_source(
                    source=resolved_source,
                    tenant_id=user.tenant_id,
                    user_id=user.user_id,
                    is_admin=user.is_admin_or_owner,
                )
                source_duration = extractor.get_video_duration(str(source_path))
            except HTTPException:
                # Unauthorized or not found: do not probe or leak peer media metadata
                source_duration = None

    valid, error = extractor.validate_timestamps(
        request.start_time,
        request.end_time,
        source_duration,
    )

    start_s = 0.0
    end_s = 0.0
    try:
        start_s = ClipExtractor._timestamp_to_seconds(request.start_time)
        end_s = ClipExtractor._timestamp_to_seconds(request.end_time)
    except ValueError:
        pass

    return ClipValidateResponse(
        valid=valid,
        error=error,
        start_seconds=start_s,
        end_seconds=end_s,
        duration_seconds=max(end_s - start_s, 0),
    )


@router.get(
    "/{clip_id}/download",
    summary="Download extracted clip",
    description="Download a previously extracted clip file.",
)
async def download_clip(
    clip_id: str,
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """Serve a clip file for download with strict tenant isolation and path validation."""
    clean_id = clip_id.strip()
    if not _SAFE_ID_PATTERN.match(clean_id) or len(clean_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid clip identifier")

    # 1. Search database-backed persistent artifacts first
    stmt = select(MediaArtifactDB).where(
        MediaArtifactDB.tenant_id == user.tenant_id,
        (MediaArtifactDB.id == clean_id) | (MediaArtifactDB.storage_path.like(f"%{clean_id}%"))
    )
    res = await db.execute(stmt)
    artifact = await safe_scalar_first(res)

    if artifact:
        # Intra-tenant member isolation: members cannot download artifacts owned by other members
        if not user.is_admin_or_owner and artifact.user_id != user.user_id:
            raise HTTPException(status_code=404, detail=f"Clip {clean_id} not found")

        if artifact.status != "ready":
            raise HTTPException(
                status_code=404 if artifact.status == "deleted" else 400,
                detail=f"Clip artifact is {artifact.status}, not ready for download",
            )

        tenant_root = get_tenant_storage_root(user.tenant_id)
        resolved = (tenant_root / artifact.storage_path).resolve()
        if not resolved.is_file() or not resolved.is_relative_to(tenant_root) or resolved.is_symlink():
            raise HTTPException(status_code=404, detail=f"Clip {clean_id} not found on disk")

        return FileResponse(
            path=str(resolved),
            filename=artifact.filename,
            media_type="application/octet-stream",
        )

    # 2. Legacy fallback handling for un-migrated / local disk files:
    # Disabled by default (fail-closed HTTP 404) where no authoritative ownership record exists.
    # The actual files on disk remain preserved.
    allow_legacy = os.environ.get("MEDIAFORGE_ENABLE_LEGACY_MEDIA_FALLBACK", "0").lower() in ("1", "true", "yes")
    if not allow_legacy or not user.is_admin_or_owner:
        raise HTTPException(status_code=404, detail=f"Clip {clean_id} not found")

    search_dirs = [get_tenant_media_dir(user.tenant_id, "clips")]
    try:
        extractor = get_clip_extractor()
        legacy_dir = Path(extractor._output_dir) / user.tenant_id
        if legacy_dir not in search_dirs:
            search_dirs.append(legacy_dir)
    except Exception:
        pass

    for tenant_clip_dir in search_dirs:
        if tenant_clip_dir.exists():
            for f in tenant_clip_dir.glob(f"*{clean_id}*"):
                if f.is_file() and not f.is_symlink() and not f.name.startswith("_temp_"):
                    resolved = f.resolve()
                    if resolved.is_relative_to(tenant_clip_dir.resolve()) and not resolved.is_symlink():
                        return FileResponse(
                            path=str(resolved),
                            filename=f.name,
                            media_type="application/octet-stream",
                        )

    raise HTTPException(
        status_code=404,
        detail=f"Clip {clean_id} not found",
    )