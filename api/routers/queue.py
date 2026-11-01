from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import CurrentUser, get_app, get_current_user, require_system_admin
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
    description="Pause the download engine (system admin only).",
    dependencies=[Depends(require_system_admin())],
)
async def pause_queue(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Pause the download queue globally."""
    app.pause()
    return {"success": True, "message": "Queue paused"}


@router.post(
    "/resume",
    summary="Resume queue",
    description="Resume the download engine (system admin only).",
    dependencies=[Depends(require_system_admin())],
)
async def resume_queue(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Resume the download queue globally."""
    app.resume()
    return {"success": True, "message": "Queue resumed"}


@router.post(
    "/start-all",
    summary="Start all deferred downloads",
    description="Move deferred jobs to the queue for the current tenant.",
)
async def start_all_deferred(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Start all deferred download jobs for tenant/user."""
    scoped_user_id = user.user_id if user.role == "member" else None
    count = app.start_all_deferred(tenant_id=user.tenant_id, user_id=scoped_user_id)
    return {"success": True, "message": f"Started {count} download(s)", "started": count}
