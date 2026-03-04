"""Usage tracking schemas."""

from pydantic import BaseModel, Field


class UsageSummaryResponse(BaseModel):
    """Usage summary for current user."""

    success: bool = True
    user_id: str
    tenant_id: str
    tier: str
    today: dict[str, int] = Field(description="Today's usage counters")
    limits: dict[str, dict] = Field(description="Limits with remaining quota")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "user_id": "system",
                    "tenant_id": "default",
                    "tier": "platinum",
                    "today": {
                        "daily_downloads": 12,
                        "bytes_downloaded": 5368709120,
                    },
                    "limits": {
                        "daily_downloads": {
                            "used": 12,
                            "limit": -1,
                            "remaining": -1,
                        }
                    },
                }
            ]
        }
    }


class UsageHistoryResponse(BaseModel):
    """Usage history over time."""

    success: bool = True
    user_id: str
    days: list[dict]
    total_downloads: int = 0
    total_bytes: int = 0
