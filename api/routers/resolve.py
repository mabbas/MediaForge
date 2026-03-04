"""URL resolve / media info endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.dependencies import CurrentUser, get_app, get_current_user
from src.grabitdown import GrabItDown

router = APIRouter(prefix="/resolve", tags=["Videos"])


class ResolveRequest(BaseModel):
    url: str


@router.post(
    "",
    summary="Resolve URL",
    description="Get media info for a URL without downloading.",
)
async def resolve_url(
    body: ResolveRequest,
    app: GrabItDown = Depends(get_app),
    user: CurrentUser = Depends(get_current_user),
):
    """Resolve URL and return media info."""
    return app.get_info(body.url)
