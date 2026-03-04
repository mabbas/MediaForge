"""GrabItDown bandwidth throttle — limits download speed per-download and globally."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class BandwidthThrottle:
    """Token-bucket based bandwidth limiter.

    Supports:
    - Per-download speed limit
    - Global speed limit across all downloads
    - Dynamic limit changes at runtime

    Usage:
        throttle = BandwidthThrottle(limit_bytes_per_second=5242880)  # 5 MB/s
        throttle.acquire(chunk_size)  # blocks if needed
    """

    def __init__(self, limit_bytes_per_second: int = 0) -> None:
        """Initialize throttle.

        Args:
            limit_bytes_per_second: Speed limit. 0 = unlimited.
        """
        self._limit = limit_bytes_per_second
        self._tokens = 0.0
        self._last_time = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, num_bytes: int) -> None:
        """Acquire permission to transfer num_bytes.

        Blocks until enough bandwidth is available.
        Returns immediately if limit is 0 (unlimited).
        """
        if self._limit <= 0:
            return

        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_time
            self._last_time = now

            # Add tokens based on elapsed time
            self._tokens += elapsed * self._limit
            # Cap tokens at 1 second worth (prevent burst after long idle)
            self._tokens = min(self._tokens, float(self._limit))

            if self._tokens >= num_bytes:
                self._tokens -= num_bytes
                return

            # Need to wait
            deficit = num_bytes - self._tokens
            wait_time = deficit / self._limit
            self._tokens = 0.0

        # Sleep outside lock
        if wait_time > 0:
            time.sleep(wait_time)

    @property
    def limit(self) -> int:
        """Current speed limit in bytes/second. 0 = unlimited."""
        return self._limit

    def set_limit(self, bytes_per_second: int) -> None:
        """Change speed limit at runtime."""
        with self._lock:
            old = self._limit
            self._limit = bytes_per_second
            self._tokens = 0.0
            self._last_time = time.monotonic()
        logger.info("Bandwidth limit changed: %s -> %s B/s", old, bytes_per_second)


class GlobalBandwidthThrottle:
    """Global bandwidth limiter shared across all downloads.

    Each download calls acquire() on this shared instance.
    The total bandwidth across all downloads is capped.
    """

    _instance: "GlobalBandwidthThrottle | None" = None
    _lock = threading.Lock()

    def __init__(self, limit_bytes_per_second: int = 0) -> None:
        self._throttle = BandwidthThrottle(limit_bytes_per_second)

    @classmethod
    def get_instance(
        cls,
        limit_bytes_per_second: int = 0,
    ) -> "GlobalBandwidthThrottle":
        """Singleton access."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(limit_bytes_per_second)
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def acquire(self, num_bytes: int) -> None:
        """Acquire bandwidth for num_bytes."""
        self._throttle.acquire(num_bytes)

    def set_limit(self, bytes_per_second: int) -> None:
        """Change global speed limit."""
        self._throttle.set_limit(bytes_per_second)

    @property
    def limit(self) -> int:
        """Current global limit in bytes/second."""
        return self._throttle.limit
