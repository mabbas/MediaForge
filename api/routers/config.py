"""Configuration management endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from api.dependencies import CurrentUser, get_current_user, get_app
from api.schemas.config import (
    ConfigResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
)
from src.config import get_settings
from src.grabitdown import GrabItDown

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["Config"])


@router.get(
    "",
    response_model=ConfigResponse,
    summary="Get configuration",
    description="Get current GrabItDown configuration.",
)
async def get_config(
    user: CurrentUser = Depends(get_current_user),
):
    """Get current configuration."""
    settings = get_settings()
    return ConfigResponse(
        app={
            "name": settings.app.name,
            "version": settings.app.version,
            "environment": settings.app.environment,
            "debug": settings.app.debug,
        },
        download={
            "output_directory": settings.download.output_directory,
            "max_concurrent_downloads": settings.download.max_concurrent_downloads,
            "absolute_max_concurrent": settings.download.absolute_max_concurrent,
            "queue_max_size": settings.download.queue_max_size,
            "retry_max_attempts": settings.download.retry_max_attempts,
            "max_file_size_mb": settings.download.max_file_size_mb,
            "min_disk_space_mb": settings.download.min_disk_space_mb,
        },
        video={
            "default_quality": settings.video.default_quality,
            "preferred_format": settings.video.preferred_format,
            "embed_subtitles": settings.video.embed_subtitles,
        },
        audio={
            "default_format": settings.audio.default_format,
            "default_bitrate": settings.audio.default_bitrate,
        },
        transcript={
            "default_languages": settings.transcript.default_languages,
            "whisper_model": settings.transcript.whisper_model,
        },
        resume={
            "enabled": settings.resume.enabled,
            "max_auto_retries": settings.resume.max_auto_retries,
        },
        providers={
            "youtube": settings.providers.youtube.enabled,
            "facebook": settings.providers.facebook.enabled,
            "generic": settings.providers.generic.enabled,
        },
    )


@router.put(
    "",
    response_model=ConfigUpdateResponse,
    summary="Update configuration",
    description="Update runtime configuration. Only specific settings can be changed at runtime.",
)
async def update_config(
    request: ConfigUpdateRequest,
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Update runtime configuration."""
    changes = {}
    if request.max_concurrent_downloads is not None:
        app._engine.set_max_concurrent(request.max_concurrent_downloads)
        changes["max_concurrent_downloads"] = request.max_concurrent_downloads
    if request.bandwidth_limit_bps is not None:
        app.set_bandwidth_limit(request.bandwidth_limit_bps)
        changes["bandwidth_limit_bps"] = request.bandwidth_limit_bps
    if not changes:
        return ConfigUpdateResponse(message="No changes applied", changes={})
    return ConfigUpdateResponse(
        message=f"Updated {len(changes)} settings",
        changes=changes,
    )
