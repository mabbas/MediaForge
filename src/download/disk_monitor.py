"""GrabItDown disk monitor — monitors available disk space and prevents downloads when low."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class DiskMonitor:
    """Monitors disk space for download directory.

    Checks:
    - Available space before starting a download
    - Whether a file of estimated size can fit
    - Warning threshold for low disk space
    """

    def __init__(
        self,
        download_dir: str = "./downloads",
        min_space_mb: int = 500,
    ) -> None:
        """Initialize disk monitor.

        Args:
            download_dir: Directory to monitor
            min_space_mb: Minimum free space in MB. Downloads blocked below this.
        """
        self._download_dir = Path(download_dir)
        self._min_space_mb = min_space_mb
        self._download_dir.mkdir(parents=True, exist_ok=True)

    def get_free_space_bytes(self) -> int:
        """Get free disk space in bytes."""
        try:
            usage = shutil.disk_usage(self._download_dir)
            return usage.free
        except OSError as exc:
            logger.error("Failed to check disk space: %s", exc)
            return 0

    def get_free_space_mb(self) -> float:
        """Get free disk space in MB."""
        return self.get_free_space_bytes() / 1048576

    def get_free_space_gb(self) -> float:
        """Get free disk space in GB."""
        return self.get_free_space_bytes() / 1073741824

    def has_enough_space(self, needed_bytes: int = 0) -> bool:
        """Check if there's enough space.

        Checks both minimum threshold and needed_bytes for a specific download.
        """
        free = self.get_free_space_bytes()
        min_required = self._min_space_mb * 1048576

        if free < min_required:
            return False

        if needed_bytes > 0 and free < (needed_bytes + min_required):
            return False

        return True

    def check_before_download(
        self,
        estimated_size_bytes: int | None = None,
    ) -> tuple[bool, str]:
        """Pre-download space check.

        Returns:
            (can_proceed, reason_message)
        """
        free_mb = self.get_free_space_mb()

        if free_mb < self._min_space_mb:
            return (
                False,
                f"Low disk space: {free_mb:.0f} MB free (minimum: {self._min_space_mb} MB)",
            )

        if estimated_size_bytes:
            estimated_mb = estimated_size_bytes / 1048576
            if not self.has_enough_space(estimated_size_bytes):
                return (
                    False,
                    f"Not enough space: {free_mb:.0f} MB free, "
                    f"need ~{estimated_mb:.0f} MB + {self._min_space_mb} MB reserve",
                )

        return (True, f"OK ({free_mb:.0f} MB free)")

    def get_stats(self) -> dict:
        """Disk usage statistics."""
        try:
            usage = shutil.disk_usage(self._download_dir)
            return {
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "total_gb": round(usage.total / 1073741824, 2),
                "used_gb": round(usage.used / 1073741824, 2),
                "free_gb": round(usage.free / 1073741824, 2),
                "usage_percent": round(usage.used / usage.total * 100, 1),
                "download_dir": str(self._download_dir),
                "min_space_mb": self._min_space_mb,
            }
        except OSError:
            return {"error": "Unable to read disk usage"}
