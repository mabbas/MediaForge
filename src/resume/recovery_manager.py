"""GrabItDown recovery manager — handles startup recovery and download resumption."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from src.config import get_settings
from src.resume.integrity_checker import IntegrityChecker
from src.resume.part_file_manager import PartFileManager, PartFileMetadata, PartFileProgress

logger = logging.getLogger(__name__)


class RecoverableDownload:
    """Represents a download that can be resumed."""

    def __init__(
        self,
        job_id: str,
        metadata: PartFileMetadata,
        progress: Optional[PartFileProgress],
        part_file_size: int,
        part_file_path: Path,
        integrity_valid: bool,
        resume_from_byte: int,
        integrity_reason: str,
    ) -> None:
        self.job_id = job_id
        self.metadata = metadata
        self.progress = progress
        self.part_file_size = part_file_size
        self.part_file_path = part_file_path
        self.integrity_valid = integrity_valid
        self.resume_from_byte = resume_from_byte
        self.integrity_reason = integrity_reason

    @property
    def percent_complete(self) -> float:
        """Return percent complete based on total_bytes."""
        if self.metadata.total_bytes and self.metadata.total_bytes > 0:
            return (self.resume_from_byte / self.metadata.total_bytes) * 100
        return 0.0

    @property
    def can_resume(self) -> bool:
        """Return True if this download is safe to resume."""
        return self.integrity_valid and self.resume_from_byte > 0


class RecoveryManager:
    """Manages startup recovery and download resumption."""

    def __init__(
        self,
        part_file_manager: Optional[PartFileManager] = None,
        integrity_checker: Optional[IntegrityChecker] = None,
    ) -> None:
        settings = get_settings()

        self._part_manager = part_file_manager or PartFileManager(
            in_progress_dir=str(Path(settings.download.output_directory) / "in_progress")
        )
        self._integrity_checker = integrity_checker or IntegrityChecker()

    def scan_incomplete(self) -> List[RecoverableDownload]:
        """Scan for incomplete downloads and verify integrity."""
        incomplete = self._part_manager.list_incomplete_downloads()
        recoverable: List[RecoverableDownload] = []

        for item in incomplete:
            job_id = item["job_id"]
            metadata: Optional[PartFileMetadata] = item["metadata"]
            progress: Optional[PartFileProgress] = item["progress"]
            part_size: int = item["part_file_size"]
            part_path: Path = item["part_file_path"]

            if metadata is None:
                logger.warning("No metadata for %s, skipping", job_id)
                continue

            expected_bytes = progress.bytes_downloaded if progress else part_size

            is_valid, reason, safe_position = self._integrity_checker.check_part_file(part_path, expected_bytes)

            recoverable.append(
                RecoverableDownload(
                    job_id=job_id,
                    metadata=metadata,
                    progress=progress,
                    part_file_size=part_size,
                    part_file_path=part_path,
                    integrity_valid=is_valid,
                    resume_from_byte=safe_position,
                    integrity_reason=reason,
                )
            )

            logger.info(
                "Found incomplete: %s (%d bytes, valid=%s)",
                job_id[:8],
                safe_position,
                is_valid,
            )

        return recoverable

    def cleanup_stale_downloads(self, max_age_days: Optional[int] = None) -> int:
        """Remove stale part files."""
        settings = get_settings()
        age = max_age_days or settings.resume.max_part_file_age_days
        return self._part_manager.cleanup_stale(age)

    def cleanup_download(self, job_id: str) -> None:
        """Remove all files for a specific incomplete download."""
        self._part_manager.cleanup(job_id)

    @property
    def part_file_manager(self) -> PartFileManager:
        """Access the underlying part file manager."""
        return self._part_manager

