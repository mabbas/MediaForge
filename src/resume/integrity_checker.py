"""GrabItDown integrity checker — verifies part file integrity before resume."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class IntegrityChecker:
    """Verifies integrity of partial downloads before resuming."""

    def check_part_file(
        self,
        part_file_path: Path,
        expected_bytes: int,
        tolerance_bytes: int = 1024,
    ) -> tuple[bool, str, int]:
        """Check part file integrity."""
        if not part_file_path.exists():
            return False, "Part file not found", 0

        actual_size = part_file_path.stat().st_size

        if actual_size == 0:
            return True, "Empty file, start fresh", 0

        size_diff = abs(actual_size - expected_bytes)

        # Treat files that are slightly smaller than expected as valid,
        # and any file larger than expected as requiring truncation.
        if actual_size == expected_bytes or (actual_size < expected_bytes and size_diff <= tolerance_bytes):
            return True, f"Size OK ({actual_size} bytes)", actual_size

        if actual_size < expected_bytes:
            return (
                True,
                f"File smaller than expected ({actual_size} < {expected_bytes}), resuming from actual size",
                actual_size,
            )

        if actual_size > expected_bytes:
            return (
                False,
                f"File larger than expected ({actual_size} > {expected_bytes}), needs truncation",
                expected_bytes,
            )

        return True, "OK", actual_size

    def hash_last_chunk(self, file_path: Path, chunk_size: int = 1_048_576) -> str | None:
        """Calculate SHA256 hash of the last chunk of a file."""
        if not file_path.exists():
            return None

        file_size = file_path.stat().st_size
        if file_size == 0:
            return None

        read_size = min(chunk_size, file_size)
        offset = file_size - read_size

        try:
            h = hashlib.sha256()
            with open(file_path, "rb") as f:
                f.seek(offset)
                h.update(f.read(read_size))
            return h.hexdigest()
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to hash file: %s", exc)
            return None

    def verify_hash(self, file_path: Path, expected_hash: str, chunk_size: int = 1_048_576) -> bool:
        """Verify last chunk hash matches expected."""
        actual = self.hash_last_chunk(file_path, chunk_size)
        if actual is None or expected_hash is None:
            return False
        return actual == expected_hash

    def truncate_to_position(self, file_path: Path, position: int) -> bool:
        """Truncate file to a specific byte position."""
        try:
            with open(file_path, "r+b") as f:
                f.truncate(position)
            logger.info("Truncated %s to %d bytes", file_path, position)
            return True
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to truncate: %s", exc)
            return False

