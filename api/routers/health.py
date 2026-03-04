"""Health and system endpoints."""

import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.schemas.common import (
    DiskUsageResponse,
    HealthResponse,
    ReadyResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])
_start_time = time.time()


@router.get(
    "",
    response_model=HealthResponse,
    summary="Liveness check",
    description="Basic health check. Returns 200 if the API is running.",
)
async def health():
    from src import __version__

    return HealthResponse(
        status="healthy",
        version=__version__,
        uptime_seconds=round(time.time() - _start_time, 1),
    )


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness check",
    description="Comprehensive readiness check verifying all dependencies.",
)
async def readiness(
    session: AsyncSession = Depends(get_session),
):
    from src import __version__
    from src.config import get_settings

    settings = get_settings()
    overall = "healthy"

    db_status = "ok"
    try:
        result = await session.execute(text("SELECT 1"))
        result.scalar()
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"
        overall = "unhealthy"

    import shutil

    ffmpeg_status = "ok" if shutil.which("ffmpeg") else "not found"

    yt_dlp_status = "not installed"
    try:
        import yt_dlp

        yt_dlp_status = yt_dlp.version.__version__
    except ImportError:
        overall = "unhealthy"

    disk_free = 0.0
    try:
        from src.download.disk_monitor import DiskMonitor

        monitor = DiskMonitor(
            min_space_mb=settings.download.min_disk_space_mb,
        )
        stats = monitor.get_stats()
        if "error" not in stats:
            disk_free = stats.get("free_gb", 0.0)
            if disk_free < 1.0:
                overall = "degraded"
    except Exception:
        pass

    providers_count = 0
    try:
        from src.core.provider_factory import create_provider_registry

        registry = create_provider_registry()
        providers_count = registry.media_provider_count
    except Exception:
        pass

    return ReadyResponse(
        status=overall,
        version=__version__,
        uptime_seconds=round(time.time() - _start_time, 1),
        database=db_status,
        ffmpeg=ffmpeg_status,
        yt_dlp=yt_dlp_status,
        disk_free_gb=disk_free,
        providers_count=providers_count,
    )


@router.get(
    "/disk",
    response_model=DiskUsageResponse,
    summary="Disk usage",
    description="Disk usage for download directory.",
)
async def disk_usage():
    from src.config import get_settings
    from src.download.disk_monitor import DiskMonitor

    settings = get_settings()
    monitor = DiskMonitor(
        download_dir=settings.download.output_directory,
        min_space_mb=settings.download.min_disk_space_mb,
    )
    stats = monitor.get_stats()
    if "error" in stats:
        return DiskUsageResponse(
            total_gb=0.0,
            used_gb=0.0,
            free_gb=0.0,
            usage_percent=0.0,
            download_dir=settings.download.output_directory,
            min_space_mb=settings.download.min_disk_space_mb,
        )
    return DiskUsageResponse(
        total_gb=stats["total_gb"],
        used_gb=stats["used_gb"],
        free_gb=stats["free_gb"],
        usage_percent=stats["usage_percent"],
        download_dir=stats["download_dir"],
        min_space_mb=stats["min_space_mb"],
    )


@router.get(
    "/network",
    summary="Network status",
    description="Check network connectivity.",
)
async def network_status():
    """Check network status."""
    from src.resume.network_monitor import NetworkMonitor

    monitor = NetworkMonitor(timeout=3.0)
    is_online = monitor.check_now()
    return {
        "status": "connected" if is_online else "disconnected",
        "online": is_online,
    }
