"""GrabItDown chunk manager — splits large downloads into chunks for multi-connection acceleration.

This is the IDM-style parallel download feature.
NOT all providers benefit from this (YouTube throttles multi-connection). Configurable per-provider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ChunkState(str, Enum):
    """State of a download chunk."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DownloadChunk:
    """Represents a byte-range chunk of a download."""

    chunk_id: int
    start_byte: int
    end_byte: int
    state: ChunkState = ChunkState.PENDING
    bytes_downloaded: int = 0

    @property
    def total_bytes(self) -> int:
        """Total bytes in this chunk."""
        return self.end_byte - self.start_byte + 1

    @property
    def is_complete(self) -> bool:
        """True if chunk finished successfully."""
        return self.state == ChunkState.COMPLETED

    @property
    def percent(self) -> float:
        """Download progress percent for this chunk."""
        if self.total_bytes == 0:
            return 0.0
        return self.bytes_downloaded / self.total_bytes * 100

    @property
    def range_header(self) -> str:
        """HTTP Range header value for resuming this chunk."""
        resume_from = self.start_byte + self.bytes_downloaded
        return f"bytes={resume_from}-{self.end_byte}"


class ChunkPlan:
    """Plan for splitting a file into chunks."""

    def __init__(
        self,
        total_bytes: int,
        num_connections: int = 1,
        chunk_size_mb: int = 10,
        min_file_size_mb: int = 100,
    ) -> None:
        """Create a chunk plan.

        Args:
            total_bytes: Total file size
            num_connections: Number of parallel connections
            chunk_size_mb: Size per chunk in MB (unused when splitting by connections)
            min_file_size_mb: Don't chunk files smaller than this
        """
        self.total_bytes = total_bytes
        self.num_connections = num_connections
        self.chunks: list[DownloadChunk] = []

        if total_bytes < (min_file_size_mb * 1048576) or num_connections <= 1:
            # Single chunk — no splitting
            self.chunks = [
                DownloadChunk(
                    chunk_id=0,
                    start_byte=0,
                    end_byte=total_bytes - 1,
                )
            ]
        else:
            # Split into chunks per connection
            bytes_per_conn = total_bytes // num_connections

            for i in range(num_connections):
                start = i * bytes_per_conn
                end = (
                    (i + 1) * bytes_per_conn - 1
                    if i < num_connections - 1
                    else total_bytes - 1
                )
                self.chunks.append(
                    DownloadChunk(
                        chunk_id=i,
                        start_byte=start,
                        end_byte=end,
                    )
                )

    @property
    def total_downloaded(self) -> int:
        """Total bytes downloaded across all chunks."""
        return sum(c.bytes_downloaded for c in self.chunks)

    @property
    def overall_percent(self) -> float:
        """Overall progress percent."""
        if self.total_bytes == 0:
            return 0.0
        return self.total_downloaded / self.total_bytes * 100

    @property
    def is_complete(self) -> bool:
        """True when all chunks are completed."""
        return all(c.is_complete for c in self.chunks)

    @property
    def pending_chunks(self) -> list[DownloadChunk]:
        """Chunks not yet started."""
        return [c for c in self.chunks if c.state == ChunkState.PENDING]

    @property
    def active_chunks(self) -> list[DownloadChunk]:
        """Chunks currently downloading."""
        return [c for c in self.chunks if c.state == ChunkState.DOWNLOADING]

    @property
    def failed_chunks(self) -> list[DownloadChunk]:
        """Chunks that failed."""
        return [c for c in self.chunks if c.state == ChunkState.FAILED]

    @property
    def chunk_count(self) -> int:
        """Number of chunks."""
        return len(self.chunks)
