"""Clip extraction API endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api.schemas.clips import (
    ClipExtractRequest,
    ClipResultResponse,
    ClipValidateRequest,
    ClipValidateResponse,
)
from src.clip.extractor import (
    ClipExtractor,
    ClipRequest,
    get_clip_extractor,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/clips",
    tags=["clips"],
)


@router.post(
    "/extract",
    response_model=ClipResultResponse,
    summary="Extract a clip from video",
    description="Extract a clip from a local file or URL using start/end timestamps. Supports fast (keyframe) and precise (re-encode) modes.",
)
async def extract_clip(request: ClipExtractRequest):
    """Extract a clip from a video."""
    extractor = get_clip_extractor()

    valid, error = extractor.validate_timestamps(
        request.start_time, request.end_time
    )
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    is_url = request.source.startswith("http://") or request.source.startswith(
        "https://"
    )

    if not is_url and not os.path.exists(request.source):
        raise HTTPException(
            status_code=404,
            detail=f"Source file not found: {request.source}",
        )

    clip_req = ClipRequest(
        source=request.source,
        start_time=request.start_time,
        end_time=request.end_time,
        output_format=request.output_format,
        mode=request.mode,
        resolution=request.resolution,
        video_bitrate=request.video_bitrate,
        audio_bitrate=request.audio_bitrate,
        is_url=is_url,
    )

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, extractor.extract, clip_req)

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=result.error or "Extraction failed",
        )

    return ClipResultResponse(
        success=result.success,
        clip_id=result.clip_id,
        source=result.source,
        output_path=result.output_path,
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
async def validate_timestamps(request: ClipValidateRequest):
    """Validate timestamps for clip extraction."""
    extractor = get_clip_extractor()

    source_duration = None
    if request.source and os.path.exists(request.source):
        source_duration = extractor.get_video_duration(request.source)

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
async def download_clip(clip_id: str):
    """Serve a clip file for download."""
    extractor = get_clip_extractor()
    clip_dir = extractor._output_dir

    for f in Path(clip_dir).glob(f"*{clip_id}*"):
        if f.is_file() and not f.name.startswith("_temp_"):
            return FileResponse(
                path=str(f),
                filename=f.name,
                media_type="application/octet-stream",
            )

    raise HTTPException(
        status_code=404,
        detail=f"Clip {clip_id} not found",
    )
