"""Transcript endpoints."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.database.models import TranscriptDB
from api.dependencies import CurrentUser, get_app, get_current_user, require_scope
from api.security.url_policy import validate_url
from src.exceptions import (
    TranscriptBotDetectionError,
    TranscriptError,
    TranscriptNotAvailableError,
    TranscriptRateLimitError,
)
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
    user: CurrentUser = Depends(require_scope("transcripts:read")),
):
    """List saved transcripts."""
    query = select(TranscriptDB)
    count_query = select(func.count(TranscriptDB.id))

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
    user: CurrentUser = Depends(require_scope("transcripts:read")),
):
    """Return available languages and formats (e.g. { \"en\": [\"srt\", \"vtt\"] })."""
    validate_url(url)
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
    """Extract YouTube transcript via yt-dlp. Blocks."""
    from src.config import get_settings
    from src.transcript.youtube_captions import extract_youtube_transcript

    settings = get_settings()
    cookies = settings.providers.youtube.cookies_file
    return extract_youtube_transcript(
        url=url,
        language=language,
        output_format=output_format,
        prefer_script=prefer_script,
        cookies_file=cookies,
    )


@router.post(
    "/extract",
    summary="Extract transcript",
    description="Extract transcript/subtitles for a video URL and language.",
)
async def extract_transcript(
    body: ExtractTranscriptRequest,
    user: CurrentUser = Depends(require_scope("transcripts:write")),
):
    """Extract transcript via yt-dlp and return content. On 429 we return immediately; client shows countdown."""
    validate_url(body.url)
    prefer_script = (body.script or "default").strip().lower() or "default"

    if prefer_script not in ("default", "roman", "native"):
        prefer_script = "default"

    from src.transcript.romanize import is_indic_roman_language

    if prefer_script == "default" and is_indic_roman_language(body.language):
        prefer_script = "roman"

    try:
        content, fmt = await asyncio.to_thread(
            _extract_transcript_sync,
            body.url,
            body.language,
            body.format,
            prefer_script if prefer_script != "default" else None,
        )
    except TranscriptNotAvailableError as e:
        logger.warning("Transcript extract 404: %s", e)
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TranscriptRateLimitError as e:
        logger.warning("Transcript extract 429: %s", e)
        raise HTTPException(
            status_code=429,
            detail=str(e) or "YouTube rate limit. Try again in 3 minutes.",
        ) from e
    except TranscriptBotDetectionError as e:
        logger.warning("Transcript extract 403: %s", e)
        raise HTTPException(status_code=403, detail=str(e)) from e
    except TranscriptError as e:
        logger.warning("Transcript extract 400: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
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
    # Record commercial subtitle retrieval usage (STEP 02-E2B-Fix-A)
    # Subtitle downloads are categorized as subtitle_retrieval, not expensive AI speech-to-text
    from api.services.usage_service import record_subtitle_usage
    try:
        tr_id = f"tr_{abs(hash(body.url + body.language)) % 10000000000000000}"
        words = len(content.split()) if content else 0
        b_count = len(content.encode("utf-8")) if content else 0
        await record_subtitle_usage(
            tenant_id=user.tenant_id,
            transcript_id=tr_id,
            word_count=words,
            bytes_count=b_count,
            user_id=user.user_id,
            api_key_id=user.api_key_id,
            auth_method=user.auth_method.value if hasattr(user.auth_method, "value") else str(user.auth_method),
            language=body.language,
            format=fmt,
        )
    except Exception as exc:
        logger.warning("Could not record subtitle retrieval usage: %s", exc)

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
    user: CurrentUser = Depends(require_scope("transcripts:read")),
):
    """Get a saved transcript."""
    transcript = await session.get(TranscriptDB, transcript_id)

    if not transcript or transcript.tenant_id != user.tenant_id:
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
