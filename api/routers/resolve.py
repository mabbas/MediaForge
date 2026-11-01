"""URL resolve / media info endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.dependencies import CurrentUser, get_app, get_current_user, require_scope
from api.security.url_policy import validate_url
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
    user: CurrentUser = Depends(require_scope("downloads:read")),
):
    """Resolve URL and return media info."""
    validate_url(body.url)
    return app.get_info(body.url)