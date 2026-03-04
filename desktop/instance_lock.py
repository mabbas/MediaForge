"""Single-instance lock — prevents multiple copies of the desktop app."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class InstanceLock:
    """Ensures only one instance of GrabItDown runs."""

    def __init__(self, data_dir: str) -> None:
        self._lock_file = Path(data_dir) / ".instance.lock"
        self._locked = False

    def acquire(self) -> bool:
        """Try to acquire the instance lock. Returns True if this is the only instance."""
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        if self._lock_file.exists():
            try:
                pid = int(self._lock_file.read_text().strip())
                if self._is_process_running(pid):
                    logger.warning(
                        "Another instance running (PID: %s)", pid
                    )
                    return False
                logger.info("Removing stale lock file")
            except (ValueError, OSError):
                pass
        self._lock_file.write_text(str(os.getpid()))
        self._locked = True
        logger.info("Instance lock acquired (PID: %s)", os.getpid())
        return True

    def release(self) -> None:
        """Release the instance lock."""
        if self._locked and self._lock_file.exists():
            try:
                self._lock_file.unlink()
                self._locked = False
                logger.info("Instance lock released")
            except OSError:
                pass

    @staticmethod
    def _is_process_running(pid: int) -> bool:
        """Check if a process with given PID exists."""
        try:
            import psutil
            return psutil.pid_exists(pid)
        except ImportError:
            pass
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
