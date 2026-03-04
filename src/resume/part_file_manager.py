"""GrabItDown part file manager — manages .part files and metadata for resumable downloads."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class PartFileMetadata(BaseModel):
    """Metadata stored alongside .part files."""

    job_id: str
    url: str
    stream_url: Optional[str] = None
    stream_url_expires: Optional[str] = None
    provider: str
    total_bytes: Optional[int] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    filename: str
    quality: Optional[str] = None
    format: Optional[str] = None
    media_type: str
    output_directory: str
    created_at: str
    updated_at: str


class PartFileProgress(BaseModel):
    """Progress state persisted periodically during download."""

    job_id: str
    bytes_downloaded: int = 0
    total_bytes: Optional[int] = None
    percent: float = 0.0
    last_byte_position: int = 0
    last_updated: str
    speed_history: List[float] = []
    retry_count: int = 0
    connection_drops: int = 0
    chunks_verified: int = 0


class PartFileManager:
    """Manages .part files for resumable downloads."""

    def __init__(self, in_progress_dir: str = "./downloads/in_progress", part_extension: str = ".part") -> None:
        """Initialize part file manager."""
        self._in_progress_dir = Path(in_progress_dir)
        self._in_progress_dir.mkdir(parents=True, exist_ok=True)
        self._part_ext = part_extension

    def create_part_file(self, job_id: str, metadata: PartFileMetadata) -> Path:
        """Create a new .part file and save metadata."""
        part_path = self._get_part_path(job_id)
        meta_path = self._get_meta_path(job_id)

        part_path.touch()
        meta_path.write_text(metadata.model_dump_json(indent=2))

        logger.debug("Created part file: %s", part_path)
        return part_path

    def update_progress(self, job_id: str, progress: PartFileProgress) -> None:
        """Persist current download progress."""
        progress_path = self._get_progress_path(job_id)
        progress.last_updated = datetime.now().isoformat()
        progress_path.write_text(progress.model_dump_json(indent=2))

    def get_metadata(self, job_id: str) -> PartFileMetadata | None:
        """Load metadata for a part file."""
        meta_path = self._get_meta_path(job_id)
        if not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text())
            return PartFileMetadata(**data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to load metadata for %s: %s", job_id, exc)
            return None

    def get_progress(self, job_id: str) -> PartFileProgress | None:
        """Load progress for a part file."""
        progress_path = self._get_progress_path(job_id)
        if not progress_path.exists():
            return None
        try:
            data = json.loads(progress_path.read_text())
            return PartFileProgress(**data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to load progress for %s: %s", job_id, exc)
            return None

    def get_part_file_path(self, job_id: str) -> Path | None:
        """Get path to .part file if it exists."""
        path = self._get_part_path(job_id)
        return path if path.exists() else None

    def get_part_file_size(self, job_id: str) -> int:
        """Get current size of .part file in bytes."""
        path = self._get_part_path(job_id)
        return path.stat().st_size if path.exists() else 0

    def complete_download(self, job_id: str, final_filename: str, output_dir: str) -> Path:
        """Move completed .part file to final destination."""
        part_path = self._get_part_path(job_id)
        if not part_path.exists():
            raise FileNotFoundError(f"Part file not found: {part_path}")

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        final_path = output_path / final_filename
        counter = 1
        while final_path.exists():
            stem = Path(final_filename).stem
            ext = Path(final_filename).suffix
            final_path = output_path / f"{stem} ({counter}){ext}"
            counter += 1

        part_path.rename(final_path)
        self._cleanup_metadata(job_id)

        logger.info("Download complete: %s", final_path)
        return final_path

    def cleanup(self, job_id: str) -> None:
        """Remove all files for a job."""
        for path in [
            self._get_part_path(job_id),
            self._get_meta_path(job_id),
            self._get_progress_path(job_id),
        ]:
            if path.exists():
                path.unlink()
                logger.debug("Removed: %s", path)

    def list_incomplete_downloads(self) -> List[dict]:
        """Scan for incomplete downloads."""
        results: List[dict] = []
        for part_file in self._in_progress_dir.glob(f"*{self._part_ext}"):
            job_id = part_file.stem
            results.append(
                {
                    "job_id": job_id,
                    "metadata": self.get_metadata(job_id),
                    "progress": self.get_progress(job_id),
                    "part_file_size": part_file.stat().st_size,
                    "part_file_path": part_file,
                }
            )
        return results

    def cleanup_stale(self, max_age_days: int = 7) -> int:
        """Remove part files older than max_age_days."""
        count = 0
        cutoff = datetime.now().timestamp() - (max_age_days * 86400)

        for part_file in self._in_progress_dir.glob(f"*{self._part_ext}"):
            if part_file.stat().st_mtime < cutoff:
                job_id = part_file.stem
                self.cleanup(job_id)
                count += 1
                logger.info("Cleaned up stale download: %s", job_id)

        return count

    # ── Private helpers ─────────────────────────────────────────────────────

    def _get_part_path(self, job_id: str) -> Path:
        return self._in_progress_dir / f"{job_id}{self._part_ext}"

    def _get_meta_path(self, job_id: str) -> Path:
        return self._in_progress_dir / f"{job_id}.meta.json"

    def _get_progress_path(self, job_id: str) -> Path:
        return self._in_progress_dir / f"{job_id}.progress.json"

    def _cleanup_metadata(self, job_id: str) -> None:
        """Remove metadata and progress files only."""
        for path in [self._get_meta_path(job_id), self._get_progress_path(job_id)]:
            if path.exists():
                path.unlink()

