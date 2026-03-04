"""Download endpoint schemas."""

from pydantic import BaseModel


class DownloadJobResponse(BaseModel):
    """Minimal job info in submit/resume response."""

    job_id: str
    status: str
    url: str = ""
    progress_percent: float = 0.0
    priority: str = "normal"


class DownloadSubmitResponse(BaseModel):
    """Response after submitting or resuming a download."""

    success: bool = True
    message: str = ""
    job: DownloadJobResponse | dict
