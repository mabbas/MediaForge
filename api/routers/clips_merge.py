"""Clip merge API endpoints (separate router so merge route is always registered)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api.schemas.merge import (
    MergeRequest as MergeRequestSchema,
    MergeResultResponse,
)
from src.clip.merger import (
    MergeRequest,
    get_clip_merger,
)

logger = logging.getLogger(__name__)

# Router prefix /clips so paths are /api/v1/clips/merge and /api/v1/clips/merge/{id}/download
router = APIRouter(prefix="/clips", tags=["clips"])


@router.post(
    "/merge",
    response_model=MergeResultResponse,
    summary="Merge multiple clips",
    description="Combine 2-20 video clips (file paths) into a single output file.",
)
async def merge_clips(request: MergeRequestSchema):
    """Merge multiple clips into one file. Clips must be local file paths (not URLs)."""
    merger = get_clip_merger()

    validated_clips = []
    for i, clip in enumerate(request.clips):
        clip = (clip or "").strip()
        if not clip:
            raise HTTPException(
                status_code=400,
                detail=f"Clip {i + 1} is empty.",
            )
        if clip.startswith("http://") or clip.startswith("https://"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Merge requires local file paths, not URLs. "
                    "Download the videos in the Download tab first, then use Browse or paste the file paths (e.g. C:\\Users\\...\\video.mp4)."
                ),
            )
        if not os.path.exists(clip):
            raise HTTPException(
                status_code=404,
                detail=f"Clip {i + 1} not found: {clip}",
            )
        validated_clips.append(clip)

    merge_req = MergeRequest(
        clips=validated_clips,
        output_format=request.output_format,
        mode=request.mode,
        resolution=request.resolution,
        video_bitrate=request.video_bitrate,
        audio_bitrate=request.audio_bitrate,
    )

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, merger.merge, merge_req)

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=result.error or "Merge failed",
        )

    return MergeResultResponse(
        success=result.success,
        merge_id=result.merge_id,
        clip_count=result.clip_count,
        output_path=result.output_path,
        total_duration_seconds=result.total_duration_seconds,
        file_size_bytes=result.file_size_bytes,
        file_size_human=result.file_size_human,
        created_at=result.created_at,
        clips_used=result.clips_used,
        error=result.error,
    )


@router.get(
    "/merge/{merge_id}/download",
    summary="Download merged file",
    description="Download a previously merged file.",
)
async def download_merged(merge_id: str):
    """Serve a merged file for download."""
    merger = get_clip_merger()
    merge_dir = merger._output_dir

    for f in Path(merge_dir).glob(f"*{merge_id}*"):
        if f.is_file():
            return FileResponse(
                path=str(f),
                filename=f.name,
                media_type="application/octet-stream",
            )

    raise HTTPException(
        status_code=404,
        detail=f"Merged file {merge_id} not found",
    )
