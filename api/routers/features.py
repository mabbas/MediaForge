"""Feature flag and tier endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import CurrentUser, get_current_user
from api.schemas.features import (
    FeatureStatusSchema,
    FeaturesResponse,
    TierComparisonResponse,
    TierInfoSchema,
    TierLimitsResponse,
)
from src.features.feature_flags import load_feature_flags
from src.features.feature_gate import FeatureGate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/features", tags=["Features"])


@router.get(
    "",
    response_model=FeaturesResponse,
    summary="Get current features",
    description="Get feature availability for the current user's tier.",
)
async def get_features(
    user: CurrentUser = Depends(get_current_user),
):
    """Get features for current tier."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    features = gate.list_all_features(tier=user.tier)
    return FeaturesResponse(
        mode=flags.mode,
        current_tier=user.tier,
        features=features,
    )


@router.get(
    "/tiers",
    response_model=TierComparisonResponse,
    summary="Compare tiers",
    description="Get all tiers with their features for comparison.",
)
async def compare_tiers(
    user: CurrentUser = Depends(get_current_user),
):
    """Compare all tiers."""
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    tiers = []
    for tier_name, tier_config in flags.tiers.items():
        features_dict = gate.list_all_features(tier=tier_name)
        features = [
            FeatureStatusSchema(name=name, enabled=enabled)
            for name, enabled in features_dict.items()
        ]
        tiers.append(
            TierInfoSchema(
                name=tier_name,
                display_name=tier_config.display_name,
                price_monthly=float(tier_config.price_monthly),
                features=features,
            )
        )
    return TierComparisonResponse(tiers=tiers, current_tier=user.tier)


@router.get(
    "/tiers/{tier_name}/limits",
    response_model=TierLimitsResponse,
    summary="Tier limits",
    description="Get detailed limits for a specific tier.",
)
async def get_tier_limits(
    tier_name: str,
):
    """Get limits for a tier."""
    flags = load_feature_flags()
    if tier_name not in flags.tiers:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Tier '{tier_name}' not found. "
                f"Available: {list(flags.tiers.keys())}"
            ),
        )
    tier = flags.tiers[tier_name]
    tf = tier.features
    limits = {
        "video_download": {
            "enabled": tf.video_download.enabled,
            "max_quality": tf.video_download.max_quality,
            "daily_limit": tf.video_download.daily_limit,
            "max_file_size_mb": tf.video_download.max_file_size_mb,
        },
        "audio_download": {
            "enabled": tf.audio_download.enabled,
            "formats": tf.audio_download.formats,
        },
        "playlist_download": {
            "enabled": tf.playlist_download.enabled,
            "max_playlist_size": tf.playlist_download.max_playlist_size,
        },
        "batch_download": {
            "enabled": tf.batch_download.enabled,
            "max_urls": tf.batch_download.max_urls,
        },
        "concurrent_downloads": {
            "enabled": tf.concurrent_downloads.enabled,
            "max_value": tf.concurrent_downloads.max_value,
        },
        "multi_connection": {
            "enabled": tf.multi_connection.enabled,
            "max_connections": tf.multi_connection.max_connections,
        },
        "transcript_youtube": {
            "enabled": tf.transcript_youtube.enabled,
        },
        "transcript_whisper": {
            "enabled": tf.transcript_whisper.enabled,
        },
        "api_access": {
            "enabled": tf.api_access.enabled,
            "rate_limit_per_hour": tf.api_access.rate_limit_per_hour,
        },
    }
    return TierLimitsResponse(tier=tier_name, limits=limits)
