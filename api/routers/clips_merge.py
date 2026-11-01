"""Clip merge API endpoints with tenant isolation and filesystem safety."""

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

from api.database.connection import get_session
from api.database.models import MediaArtifactDB
from api.dependencies import CurrentUser, get_current_user, require_scope
from api.schemas.merge import (
    MergeRequest as MergeRequestSchema,
    MergeResultResponse,
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
    MAX_MERGE_INPUTS,
    MIN_FREE_DISK_SPACE_MB,
    _SAFE_ID_PATTERN,
    generate_safe_output_path,
    get_ffmpeg_semaphore,
    get_tenant_media_dir,
    get_tenant_storage_root,
    resolve_and_authorize_clip_source,
    validate_safe_filename,
)
from src.clip.merger import (
    MergeRequest,
    get_clip_merger,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clips", tags=["clips"])


@router.post(
    "/merge",
    response_model=MergeResultResponse,
    summary="Merge multiple clips",
    description="Combine 2-20 video clips into a single output file with strict tenant isolation.",
)
async def merge_clips(
    request: MergeRequestSchema,
    user: CurrentUser = Depends(require_scope("clips:write")),
    db: AsyncSession = Depends(get_session),
):
    """Merge multiple clips into one file with tenant boundary verification."""
    if len(request.clips) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 clips are required for merging.",
        )
    if len(request.clips) > MAX_MERGE_INPUTS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_MERGE_INPUTS} clips can be merged at once.",
        )

    # 1. Validate inputs and containment first
    validated_clip_paths: list[str] = []
    display_clips: list[str] = []

    for i, clip_ref in enumerate(request.clips):
        clip_ref = (clip_ref or "").strip()
        if not clip_ref:
            raise HTTPException(
                status_code=400,
                detail=f"Clip {i + 1} reference is empty.",
            )
        if clip_ref.startswith("http://") or clip_ref.startswith("https://") or "://" in clip_ref:
            raise HTTPException(
                status_code=400,
                detail="Merge requires local media references within tenant storage, not URLs.",
            )

        # Check source ownership in database and resolve reference
        res_ref = await resolve_source_media_reference(db, clip_ref, user.tenant_id, user)
        await verify_source_media_authorization(db, res_ref, user)

        # Resolve and authorize local media reference strictly within tenant storage
        resolved_path = resolve_and_authorize_clip_source(
            source=res_ref,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            is_admin=user.is_admin_or_owner,
        )
        validated_clip_paths.append(str(resolved_path))
        display_clips.append(resolved_path.name)

    # 2. Check disk space before proceeding
    try:
        free_mb = shutil.disk_usage(get_tenant_storage_root(user.tenant_id)).free / (1024 * 1024)
        if free_mb < MIN_FREE_DISK_SPACE_MB:
            raise HTTPException(
                status_code=507,
                detail=f"Insufficient disk space ({free_mb:.0f} MB available, minimum {MIN_FREE_DISK_SPACE_MB} MB required)",
            )
    except OSError:
        pass

    from src.exceptions import FFmpegNotFoundError
    try:
        merger = get_clip_merger()
    except FFmpegNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    # 3. Generate safe collision-resistant output path in tenant merged folder
    abs_output_path, rel_output_path, mid = generate_safe_output_path(
        tenant_id=user.tenant_id,
        subfolder="merged",
        prefix=f"merged_{len(validated_clip_paths)}clips",
        extension=request.output_format,
    )

    merge_req = MergeRequest(
        clips=validated_clip_paths,
        output_path=str(abs_output_path),
        output_format=request.output_format,
        mode=request.mode,
        resolution=request.resolution,
        video_bitrate=request.video_bitrate,
        audio_bitrate=request.audio_bitrate,
        merge_id=mid,
    )

    # 4. Decoupled FFmpeg execution: persist pending artifact record before FFmpeg
    try:
        await create_artifact_record(
            db=db,
            artifact_id=mid,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            api_key_id=user.api_key_id,
            artifact_type="merge",
            storage_path=rel_output_path,
            filename=abs_output_path.name,
            format=request.output_format,
            status="processing",
            input_sources=validated_clip_paths,
            metadata_json={
                "mode": request.mode,
                "clip_count": len(validated_clip_paths),
                "resolution": request.resolution,
                "video_bitrate": request.video_bitrate,
                "audio_bitrate": request.audio_bitrate,
            },
        )
    except Exception as exc:
        logger.warning("Could not persist initial merge artifact record %s: %s", mid, exc)

    # 5. Concurrency bounding
    sem = get_ffmpeg_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=10.0)
    except asyncio.TimeoutError:
        await fail_artifact_in_fresh_session(mid, "Media processing concurrency limit reached")
        raise HTTPException(
            status_code=503,
            detail="Media processing concurrency limit reached. Please retry shortly.",
        )

    try:
        t0 = time.perf_counter()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, merger.merge, merge_req)
        processing_time_ms = max(0, int((time.perf_counter() - t0) * 1000))
    except Exception as exc:
        await fail_artifact_in_fresh_session(mid, str(exc))
        if abs_output_path.exists():
            try:
                abs_output_path.unlink()
            except OSError:
                pass
        raise HTTPException(
            status_code=500,
            detail="Video merge encountered an internal error",
        )
    finally:
        sem.release()

    if not result.success:
        await fail_artifact_in_fresh_session(mid, result.error or "Merge failed")
        if abs_output_path.exists():
            try:
                abs_output_path.unlink()
            except OSError:
                pass
        raise HTTPException(
            status_code=500,
            detail=result.error or "Merge failed",
        )

    # 6. Compute sha256 checksum and complete artifact + usage atomically in fresh session
    sha256 = compute_sha256(abs_output_path) if abs_output_path.exists() else None
    usage_kwargs = {
        "tenant_id": user.tenant_id,
        "event_type": "merge",
        "idempotency_key": f"merge:{mid}",
        "resource_id": mid,
        "quantity": 1.0,
        "unit": "count",
        "bytes": result.file_size_bytes or 0,
        "duration_seconds": result.total_duration_seconds or 0.0,
        "user_id": user.user_id,
        "api_key_id": user.api_key_id,
        "auth_method": user.auth_method.value if hasattr(user.auth_method, "value") else str(user.auth_method),
        "metadata_json": {
            "clip_count": result.clip_count,
            "clips_used": display_clips,
            "processing_time_ms": processing_time_ms,
        },
    }

    await complete_artifact_in_fresh_session(
        artifact_id=mid,
        duration_seconds=result.total_duration_seconds,
        file_size_bytes=result.file_size_bytes,
        sha256_checksum=sha256 or "",
        usage_event_kwargs=usage_kwargs,
    )

    # Return safe relative output path, never leaking absolute server paths
    return MergeResultResponse(
        success=result.success,
        merge_id=result.merge_id,
        clip_count=result.clip_count,
        output_path=rel_output_path,
        total_duration_seconds=result.total_duration_seconds,
        file_size_bytes=result.file_size_bytes,
        file_size_human=result.file_size_human,
        created_at=result.created_at,
        clips_used=display_clips,
        error=result.error,
    )


@router.get(
    "/merge/{merge_id}/download",
    summary="Download merged file",
    description="Download a previously merged file.",
)
async def download_merged(
    merge_id: str,
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """Serve a merged file for download with strict tenant isolation, member checks, and path validation."""
    clean_id = merge_id.strip()
    if not _SAFE_ID_PATTERN.match(clean_id) or len(clean_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid merge identifier")

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
            raise HTTPException(status_code=404, detail=f"Merged file {clean_id} not found")

        if artifact.status != "ready":
            raise HTTPException(
                status_code=404 if artifact.status == "deleted" else 400,
                detail=f"Merge artifact is {artifact.status}, not ready for download",
            )

        tenant_root = get_tenant_storage_root(user.tenant_id)
        resolved = (tenant_root / artifact.storage_path).resolve()
        if not resolved.is_file() or not resolved.is_relative_to(tenant_root) or resolved.is_symlink():
            raise HTTPException(status_code=404, detail=f"Merged file {clean_id} not found on disk")

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
        raise HTTPException(status_code=404, detail=f"Merged file {clean_id} not found")

    search_dirs = [get_tenant_media_dir(user.tenant_id, "merged")]
    try:
        merger = get_clip_merger()
        legacy_dir = Path(merger._output_dir) / user.tenant_id
        if legacy_dir not in search_dirs:
            search_dirs.append(legacy_dir)
    except Exception:
        pass

    for tenant_merge_dir in search_dirs:
        if tenant_merge_dir.exists():
            for f in tenant_merge_dir.glob(f"*{clean_id}*"):
                if f.is_file() and not f.is_symlink() and not f.name.startswith("_temp_"):
                    resolved = f.resolve()
                    if resolved.is_relative_to(tenant_merge_dir.resolve()) and not resolved.is_symlink():
                        return FileResponse(
                            path=str(resolved),
                            filename=f.name,
                            media_type="application/octet-stream",
                        )

    raise HTTPException(
        status_code=404,
        detail=f"Merged file {clean_id} not found",
    )