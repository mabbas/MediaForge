"""Feature flag and tier schemas."""

from pydantic import BaseModel, Field


class FeatureStatusSchema(BaseModel):
    """Single feature status."""

    name: str
    enabled: bool
    details: dict = {}

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "video_download",
                    "enabled": True,
                    "details": {
                        "max_quality": "1080p",
                        "daily_limit": 50,
                    },
                }
            ]
        }
    }


class TierInfoSchema(BaseModel):
    """Tier information."""

    name: str
    display_name: str
    price_monthly: float
    features: list[FeatureStatusSchema]


class FeaturesResponse(BaseModel):
    """Feature flags response."""

    success: bool = True
    mode: str = Field(description="personal or tiered")
    current_tier: str
    features: dict[str, bool]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "mode": "personal",
                    "current_tier": "platinum",
                    "features": {
                        "video_download": True,
                        "audio_download": True,
                        "playlist_download": True,
                        "batch_download": True,
                    },
                }
            ]
        }
    }


class TierComparisonResponse(BaseModel):
    """Compare all tiers."""

    success: bool = True
    tiers: list[TierInfoSchema]
    current_tier: str


class TierLimitsResponse(BaseModel):
    """Limits for a specific tier."""

    success: bool = True
    tier: str
    limits: dict[str, dict]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "tier": "pro",
                    "limits": {
                        "video_download": {
                            "max_quality": "1080p",
                            "daily_limit": 50,
                            "max_file_size_mb": 2048,
                        },
                        "playlist_download": {"max_playlist_size": 50},
                        "concurrent_downloads": {"max_value": 3},
                    },
                }
            ]
        }
    }
