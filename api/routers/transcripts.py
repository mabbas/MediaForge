"""Transcript endpoints."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.database.models import TranscriptDB
from api.dependencies import CurrentUser, get_app, get_current_user
from src.grabitdown import GrabItDown

router = APIRouter(prefix="/transcripts", tags=["Transcripts"])
logger = logging.getLogger(__name__)


class ExtractTranscriptRequest(BaseModel):
    """Request body for transcript extraction."""

    url: str
    language: str = "en"
    format: str = "srt"
    # For Hindi/Urdu: "default" = both, "roman" = auto/Latin script, "native" = manual/Devanagari-Urdu script
    script: str | None = None  # default | roman | native


@router.get(
    "",
    summary="List transcripts",
    description="Get saved transcripts with pagination.",
)
async def list_transcripts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    language: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """List saved transcripts."""
    query = select(TranscriptDB)
    count_query = select(func.count(TranscriptDB.id))

    if user.tenant_id != "default":
        query = query.where(TranscriptDB.tenant_id == user.tenant_id)
        count_query = count_query.where(TranscriptDB.tenant_id == user.tenant_id)
    if language:
        query = query.where(TranscriptDB.language == language)
        count_query = count_query.where(TranscriptDB.language == language)

    query = query.order_by(TranscriptDB.created_at.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await session.execute(query)
    transcripts = result.scalars().all()

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    return {
        "success": True,
        "transcripts": [
            {
                "id": t.id,
                "url": t.url,
                "language": t.language,
                "source": t.source,
                "format": t.format,
                "word_count": t.word_count,
                "segment_count": t.segment_count,
                "duration_seconds": t.duration_seconds,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in transcripts
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/languages",
    summary="List available transcript languages",
    description="Get available subtitle/transcript languages for a video URL.",
)
async def list_transcript_languages(
    url: str = Query(..., description="Video URL"),
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Return available languages and formats (e.g. { \"en\": [\"srt\", \"vtt\"] })."""
    try:
        info = await asyncio.to_thread(app.get_info, url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not get video info: {e!s}") from e
    languages = info.get("subtitles_available") or {}
    return {"success": True, "languages": languages}


def _extract_transcript_sync(
    url: str,
    language: str,
    output_format: str,
    prefer_script: str | None = None,
) -> tuple[str, str]:
    """Run yt-dlp to download subtitles and return (content, format). Blocks.

    prefer_script: "roman" = auto captions only (often Latin/Roman),
                   "native" = manual subtitles only (often Devanagari/Urdu script),
                   None/"default" = both (current behavior).
    """
    import yt_dlp

    want_manual = prefer_script != "roman"
    want_auto = prefer_script != "native"

    with tempfile.TemporaryDirectory() as tmpdir:
        out_tmpl = str(Path(tmpdir) / "%(id)s.%(ext)s")
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": want_manual,
            "writeautomaticsub": want_auto,
            "subtitleslangs": [language],
            "subtitlesformat": output_format,
            "outtmpl": {"default": out_tmpl},
            # Reduce chance of YouTube 429: longer delays (helps Urdu/other-language subtitle endpoints)
            "sleep_interval_requests": 3,
            "sleep_interval_subtitles": 6,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            all_subs = {}
            if info:
                all_subs.update(info.get("subtitles") or {})
                all_subs.update(info.get("automatic_captions") or {})
            if language not in all_subs:
                available = list(all_subs.keys())[:10]
                raise ValueError(
                    f"Language '{language}' not available. Available: {', '.join(available) or 'none'}"
                )
            ydl.download([url])
        # Find the written subtitle file (yt-dlp may use id or title in filename)
        for path in Path(tmpdir).iterdir():
            if path.suffix.lstrip(".") in (output_format, "srt", "vtt", "json3"):
                return path.read_text(encoding="utf-8", errors="replace"), path.suffix.lstrip(".")
    raise ValueError("Subtitle file was not written")


@router.post(
    "/extract",
    summary="Extract transcript",
    description="Extract transcript/subtitles for a video URL and language.",
)
async def extract_transcript(
    body: ExtractTranscriptRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Extract transcript via yt-dlp and return content. On 429 we return immediately; client shows countdown."""
    prefer_script = (body.script or "default").strip().lower() or "default"
    if prefer_script not in ("default", "roman", "native"):
        prefer_script = "default"

    try:
        content, fmt = await asyncio.to_thread(
            _extract_transcript_sync,
            body.url,
            body.language,
            body.format,
            prefer_script if prefer_script != "default" else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "Too Many Requests" in err_msg:
            logger.warning("Transcript extract 429 for language %s", body.language)
            raise HTTPException(
                status_code=429,
                detail="YouTube rate limit. Try again in 3 minutes.",
            ) from e
        raise HTTPException(status_code=500, detail=f"Extract failed: {err_msg}") from e
    return {
        "success": True,
        "url": body.url,
        "language": body.language,
        "format": fmt,
        "content": content,
    }


@router.get(
    "/{transcript_id}",
    summary="Get transcript",
    description="Get a specific saved transcript with full content.",
)
async def get_transcript(
    transcript_id: str,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
):
    """Get a saved transcript."""
    transcript = await session.get(TranscriptDB, transcript_id)

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    if (
        transcript.tenant_id
        and transcript.tenant_id != user.tenant_id
        and user.tenant_id != "default"
    ):
        raise HTTPException(status_code=404, detail="Transcript not found")

    return {
        "success": True,
        "id": transcript.id,
        "url": transcript.url,
        "language": transcript.language,
        "source": transcript.source,
        "format": transcript.format,
        "content": transcript.content,
        "word_count": transcript.word_count,
        "segment_count": transcript.segment_count,
        "duration_seconds": transcript.duration_seconds,
        "created_at": transcript.created_at.isoformat() if transcript.created_at else None,
    }
