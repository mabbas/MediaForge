"""Provider endpoints."""

from fastapi import APIRouter, Depends

from api.dependencies import CurrentUser, get_app, get_current_user, require_scope
from src.grabitdown import GrabItDown

router = APIRouter(prefix="/providers", tags=["Providers"])


@router.get(
    "",
    summary="List providers",
    description="List registered media providers.",
)
async def list_providers(
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(require_scope("providers:read")),
):
    """List providers."""
    return app.list_providers()
