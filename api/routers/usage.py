"""Usage tracking endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from api.dependencies import CurrentUser, get_current_user, get_usage_tracker
from api.schemas.usage import UsageSummaryResponse
from src.features.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/usage", tags=["Usage"])


@router.get(
    "",
    response_model=UsageSummaryResponse,
    summary="Usage summary",
    description="Get current usage and remaining quotas for the authenticated user.",
)
async def get_usage(
    user: CurrentUser = Depends(get_current_user),
    tracker: UsageTracker = Depends(get_usage_tracker),
):
    """Get current usage."""
    today_usage = tracker.get_all_usage(user.user_id)
    limits = {}
    counters = ["daily_downloads", "batch_size", "playlist_size"]
    for counter in counters:
        used = tracker.get_usage(user.user_id, counter)
        remaining = tracker.get_remaining(
            user.user_id, counter, tier=user.tier
        )
        limit_val = -1
        if remaining != -1:
            limit_val = used + remaining
        limits[counter] = {
            "used": used,
            "limit": limit_val,
            "remaining": remaining,
        }
    return UsageSummaryResponse(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        tier=user.tier,
        today=today_usage,
        limits=limits,
    )


@router.post(
    "/reset",
    response_model=dict,
    summary="Reset usage",
    description="Reset usage counters for current user (admin only in production).",
)
async def reset_usage(
    user: CurrentUser = Depends(get_current_user),
    tracker: UsageTracker = Depends(get_usage_tracker),
):
    """Reset usage counters."""
    tracker.reset(user.user_id)
    return {
        "success": True,
        "message": f"Usage reset for {user.user_id}",
    }
