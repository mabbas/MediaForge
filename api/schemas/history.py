"""Download history schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class HistoryEntrySchema(BaseModel):
    """Single history entry."""

    job_id: str
    url: str
    provider: str | None = None
    media_type: str
    status: str
    title: str | None = None
    quality: str | None = None
    file_path: str | None = None
    file_size_bytes: int | None = None
    file_size_human: str | None = None
    duration_seconds: int | None = None
    error_message: str | None = None
    user_id: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class HistoryResponse(BaseModel):
    """Paginated download history."""

    success: bool = True
    entries: list[HistoryEntrySchema]
    total: int
    page: int
    page_size: int
    total_pages: int
    summary: dict = {}

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "entries": [
                        {
                            "job_id": "abc-123",
                            "url": "https://youtube.com/...",
                            "status": "completed",
                            "title": "Video Title",
                            "file_size_human": "125.3 MB",
                        }
                    ],
                    "total": 150,
                    "page": 1,
                    "page_size": 20,
                    "total_pages": 8,
                    "summary": {
                        "total_completed": 120,
                        "total_failed": 25,
                        "total_bytes": 53687091200,
                    },
                }
            ]
        }
    }


class HistoryStatsResponse(BaseModel):
    """Download history statistics."""

    success: bool = True
    total_downloads: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    total_bytes_downloaded: int = 0
    total_bytes_human: str = "0 B"
    avg_file_size_bytes: int = 0
    avg_file_size_human: str = "0 B"
    most_used_provider: str | None = None
    most_used_quality: str | None = None
    downloads_by_provider: dict[str, int] = {}
    downloads_by_status: dict[str, int] = {}
    downloads_by_media_type: dict[str, int] = {}


class ClearHistoryResponse(BaseModel):
    """Response after clearing history."""

    success: bool = True
    message: str
    deleted_count: int
