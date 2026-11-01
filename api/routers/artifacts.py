"""Media Artifacts API endpoints (STEP 02-E1)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.database.connection import get_session
from api.dependencies import CurrentUser, require_scope
from api.schemas.artifacts import (
    ArtifactListResponse,
    MediaArtifactResponse,
    RetentionPreviewResponse,
)
from api.security.media_storage import get_tenant_storage_root
from api.services.artifact_service import (
    delete_authorized_artifact,
    get_authorized_artifact,
    list_authorized_artifacts,
    model_to_response,
)
from api.services.retention_service import generate_retention_preview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get(
    "/retention/preview",
    response_model=RetentionPreviewResponse,
    summary="Preview media retention and cleanup (dry-run)",
    description="Calculate tenant-aware dry-run retention audit under default retain-forever policy.",
)
async def preview_retention(
    retention_days: int = Query(-1, description="Retention threshold in days (-1 for default retain-forever)"),
    target_tenant_id: Optional[str] = Query(None, description="Optional tenant filter (operator only)"),
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """Generate dry-run retention preview with zero mutations."""
    if not user.is_admin_or_owner and not user.is_system_admin:
        raise HTTPException(
            status_code=403,
            detail="Retention preview requires tenant administrator or owner privileges",
        )

    # Scoped strictly to caller's tenant unless system admin
    if user.is_system_admin:
        tenant_filter = target_tenant_id
    else:
        if target_tenant_id and target_tenant_id != user.tenant_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: principal is not authorized for target tenant resources",
            )
        tenant_filter = user.tenant_id

    report = await generate_retention_preview(
        db=db,
        retention_days=retention_days,
        filter_tenant_id=tenant_filter,
    )
    # Mask internal server filesystem root
    report.storage_root = "downloads"
    return report


@router.get(
    "",
    response_model=ArtifactListResponse,
    summary="List media artifacts",
    description="List persistent media artifacts belonging to the authorized tenant and caller.",
)
async def list_artifacts(
    artifact_type: Optional[str] = Query(None, description="Filter by type ('clip' or 'merge')"),
    status: Optional[str] = Query(None, description="Filter by status ('ready', 'processing', etc.)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """List media artifacts with tenant and intra-tenant member isolation."""
    items, total = await list_authorized_artifacts(
        db=db,
        user=user,
        artifact_type=artifact_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ArtifactListResponse(
        items=[model_to_response(art) for art in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{artifact_id}",
    response_model=MediaArtifactResponse,
    summary="Get media artifact metadata",
    description="Retrieve metadata for a specific media artifact.",
)
async def get_artifact(
    artifact_id: str,
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """Retrieve an artifact with tenant and intra-tenant member isolation."""
    artifact = await get_authorized_artifact(db, artifact_id, user)
    return model_to_response(artifact)


@router.get(
    "/{artifact_id}/download",
    summary="Download media artifact file",
    description="Stream an authorized media artifact file.",
)
async def download_artifact(
    artifact_id: str,
    user: CurrentUser = Depends(require_scope("clips:read")),
    db: AsyncSession = Depends(get_session),
):
    """Download an artifact file with tenant and intra-tenant member isolation."""
    artifact = await get_authorized_artifact(db, artifact_id, user)

    if artifact.status != "ready":
        raise HTTPException(
            status_code=400,
            detail=f"Artifact status is '{artifact.status}', not ready for download",
        )

    tenant_root = get_tenant_storage_root(user.tenant_id)
    file_path = (tenant_root / artifact.storage_path).resolve()

    if not file_path.is_file() or not file_path.is_relative_to(tenant_root):
        raise HTTPException(status_code=404, detail="Artifact file not found on disk")

    return FileResponse(
        path=str(file_path),
        filename=artifact.filename,
        media_type="application/octet-stream",
    )


@router.delete(
    "/{artifact_id}",
    summary="Delete media artifact",
    description="Delete a media artifact and remove its underlying storage file.",
)
async def delete_artifact(
    artifact_id: str,
    user: CurrentUser = Depends(require_scope("clips:write")),
    db: AsyncSession = Depends(get_session),
):
    """Delete an artifact with tenant and intra-tenant member isolation."""
    await delete_authorized_artifact(db, artifact_id, user)
    return {"success": True, "detail": "Artifact deleted successfully"}
