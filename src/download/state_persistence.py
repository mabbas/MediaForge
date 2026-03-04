"""GrabItDown state persistence — saves and restores download queue state across app restarts."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from src.models.download import DownloadJob, DownloadProgress, DownloadRequest
from src.models.enums import DownloadStatus

logger = logging.getLogger(__name__)


class StatePersistence:
    """Persists download engine state to disk.

    On shutdown: saves queued jobs to JSON file.
    On startup: loads saved jobs for re-queueing.
    """

    def __init__(
        self,
        state_dir: str = "./downloads",
        state_file: str = "engine_state.json",
    ) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._state_dir / state_file

    def _job_to_dict(self, job: DownloadJob, status: str) -> dict:
        """Serialize a job to a minimal dict for re-queueing."""
        req = job.request
        return {
            "job_id": job.job_id,
            "url": req.url,
            "media_type": req.media_type.value,
            "quality": req.quality.value,
            "video_format": req.video_format.value,
            "audio_format": req.audio_format.value,
            "audio_bitrate": req.audio_bitrate,
            "output_directory": req.output_directory or "",
            "priority": job.priority,
            "user_id": job.user_id,
            "tenant_id": job.tenant_id,
            "status": status,
        }

    def save_state(
        self,
        queued_jobs: list[DownloadJob],
        active_jobs: list[DownloadJob],
    ) -> None:
        """Save current engine state to disk.

        Saves both queued and active jobs. Active jobs are saved as INTERRUPTED for resume.
        """
        state: dict = {
            "saved_at": datetime.now().isoformat(),
            "version": "1.0",
            "jobs": [],
        }

        for job in queued_jobs:
            state["jobs"].append(self._job_to_dict(job, "queued"))

        for job in active_jobs:
            state["jobs"].append(self._job_to_dict(job, "interrupted"))

        self._state_file.write_text(json.dumps(state, indent=2))
        logger.info("Saved engine state: %s jobs", len(state["jobs"]))

    def load_state(self) -> list[dict]:
        """Load saved state from disk.

        Returns list of job dicts for re-queueing.
        Returns empty list if no state file exists.
        """
        if not self._state_file.exists():
            return []

        try:
            data = json.loads(self._state_file.read_text())
            jobs = data.get("jobs", [])
            logger.info(
                "Loaded engine state: %s jobs from %s",
                len(jobs),
                data.get("saved_at", "unknown"),
            )
            return jobs
        except Exception as exc:
            logger.error("Failed to load state: %s", exc)
            return []

    def clear_state(self) -> None:
        """Remove saved state file."""
        if self._state_file.exists():
            self._state_file.unlink()
            logger.debug("State file cleared")

    def has_saved_state(self) -> bool:
        """Check if saved state exists."""
        return self._state_file.exists()
