"""Configuration schemas."""

from pydantic import BaseModel, Field


class ConfigResponse(BaseModel):
    """Current configuration response."""

    success: bool = True
    app: dict
    download: dict
    video: dict
    audio: dict
    transcript: dict
    resume: dict
    providers: dict


class ConfigUpdateRequest(BaseModel):
    """Update configuration request.

    Only specific runtime-changeable settings
    are supported.
    """

    max_concurrent_downloads: int | None = Field(
        None, ge=1, le=10, description="Max concurrent downloads"
    )
    bandwidth_limit_bps: int | None = Field(
        None, ge=0, description="Global bandwidth limit (0=unlimited)"
    )
    output_directory: str | None = Field(
        None, description="Default output directory"
    )
    default_quality: str | None = Field(
        None, description="Default video quality"
    )
    default_audio_format: str | None = Field(
        None, description="Default audio format"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "max_concurrent_downloads": 5,
                    "bandwidth_limit_bps": 10485760,
                    "default_quality": "1080p",
                }
            ]
        }
    }


class ConfigUpdateResponse(BaseModel):
    """Configuration update result."""

    success: bool = True
    message: str
    changes: dict
