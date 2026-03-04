"""Tests for GrabItDown chunk manager."""

from src.download.chunk_manager import ChunkPlan, ChunkState, DownloadChunk


def test_single_chunk_small_file():
    """Small file gets single chunk."""
    plan = ChunkPlan(
        total_bytes=5242880,  # 5MB
        num_connections=4,
        min_file_size_mb=100,
    )
    assert plan.chunk_count == 1
    assert plan.chunks[0].start_byte == 0
    assert plan.chunks[0].end_byte == 5242879


def test_single_chunk_one_connection():
    """Single connection = single chunk."""
    plan = ChunkPlan(
        total_bytes=1073741824,  # 1GB
        num_connections=1,
    )
    assert plan.chunk_count == 1


def test_multi_chunk_split():
    """Large file split into correct chunks."""
    plan = ChunkPlan(
        total_bytes=1073741824,  # 1GB
        num_connections=4,
        min_file_size_mb=100,
    )
    assert plan.chunk_count == 4

    for i in range(len(plan.chunks) - 1):
        assert plan.chunks[i].end_byte + 1 == plan.chunks[i + 1].start_byte

    assert plan.chunks[-1].end_byte == 1073741823

    total = sum(c.total_bytes for c in plan.chunks)
    assert total == 1073741824


def test_chunk_range_header():
    """Range header formatted correctly."""
    chunk = DownloadChunk(chunk_id=0, start_byte=1000, end_byte=1999)
    assert chunk.range_header == "bytes=1000-1999"


def test_chunk_range_header_after_partial():
    """Range header accounts for downloaded bytes."""
    chunk = DownloadChunk(
        chunk_id=0,
        start_byte=1000,
        end_byte=1999,
        bytes_downloaded=500,
    )
    assert chunk.range_header == "bytes=1500-1999"


def test_chunk_percent():
    """Chunk percent calculated correctly."""
    chunk = DownloadChunk(
        chunk_id=0,
        start_byte=0,
        end_byte=999,
        bytes_downloaded=500,
    )
    assert chunk.percent == 50.0


def test_plan_overall_percent():
    """Overall plan percent is correct."""
    plan = ChunkPlan(
        total_bytes=1000,
        num_connections=2,
        min_file_size_mb=0,
    )
    plan.chunks[0].bytes_downloaded = 250
    plan.chunks[1].bytes_downloaded = 250
    assert plan.overall_percent == 50.0


def test_plan_is_complete():
    """Plan complete when all chunks complete."""
    plan = ChunkPlan(
        total_bytes=1000,
        num_connections=1,
        min_file_size_mb=0,
    )
    assert plan.is_complete is False
    plan.chunks[0].state = ChunkState.COMPLETED
    assert plan.is_complete is True


def test_plan_pending_chunks():
    """Pending chunks filter works."""
    plan = ChunkPlan(
        total_bytes=1000,
        num_connections=3,
        min_file_size_mb=0,
    )
    plan.chunks[0].state = ChunkState.COMPLETED
    plan.chunks[1].state = ChunkState.DOWNLOADING
    assert len(plan.pending_chunks) == 1
    assert plan.pending_chunks[0].chunk_id == 2
