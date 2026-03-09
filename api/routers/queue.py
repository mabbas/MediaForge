"""Queue management endpoints."""

from fastapi import APIRouter, Depends

from api.dependencies import CurrentUser, get_app, get_current_user
from src.grabitdown import GrabItDown

router = APIRouter(prefix="/queue", tags=["Queue"])


@router.get(
    "/stats",
    summary="Queue statistics",
    description="Get download queue and engine statistics.",
)
async def queue_stats(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Get queue and engine stats."""
    return app.get_stats()


@router.post(
    "/pause",
    summary="Pause queue",
    description="Pause the download engine (no new downloads start).",
)
async def pause_queue(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Pause the download queue."""
    app.pause()
    return {"success": True, "message": "Queue paused"}


@router.post(
    "/resume",
    summary="Resume queue",
    description="Resume the download engine.",
)
async def resume_queue(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Resume the download queue."""
    app.resume()
    return {"success": True, "message": "Queue resumed"}


@router.post(
    "/start-all",
    summary="Start all deferred downloads",
    description="Move all deferred jobs to the queue; they will run according to simultaneous downloads.",
)
async def start_all_deferred(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Start all deferred download jobs."""
    count = app.start_all_deferred()
    return {"success": True, "message": f"Started {count} download(s)", "started": count}
