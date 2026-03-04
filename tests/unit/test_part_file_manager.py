"""Tests for GrabItDown part file manager."""

from datetime import datetime

from src.resume.part_file_manager import PartFileManager, PartFileMetadata, PartFileProgress


def test_create_part_file(tmp_path):
    """Creates .part and .meta.json files."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path))
    meta = PartFileMetadata(
        job_id="test-123",
        url="https://youtube.com/watch?v=test",
        provider="youtube",
        total_bytes=1_073_741_824,
        filename="Test Video.mp4",
        media_type="video",
        output_directory=str(tmp_path),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    part_path = mgr.create_part_file("test-123", meta)
    assert part_path.exists()
    assert part_path.suffix == ".part"

    loaded_meta = mgr.get_metadata("test-123")
    assert loaded_meta is not None
    assert loaded_meta.job_id == "test-123"
    assert loaded_meta.total_bytes == 1_073_741_824


def test_update_and_get_progress(tmp_path):
    """Progress saved and loaded correctly."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path))
    progress = PartFileProgress(
        job_id="test-123",
        bytes_downloaded=524_288_000,
        total_bytes=1_073_741_824,
        percent=48.8,
        last_byte_position=524_287_999,
        last_updated=datetime.now().isoformat(),
    )

    mgr.update_progress("test-123", progress)
    loaded = mgr.get_progress("test-123")

    assert loaded is not None
    assert loaded.bytes_downloaded == 524_288_000
    assert loaded.percent == 48.8


def test_get_part_file_size(tmp_path):
    """Reports correct file size."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path))

    part_path = tmp_path / "test-123.part"
    part_path.write_bytes(b"x" * 1000)

    assert mgr.get_part_file_size("test-123") == 1000


def test_get_part_file_size_nonexistent(tmp_path):
    """Returns 0 for nonexistent file."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path))
    assert mgr.get_part_file_size("nonexistent") == 0


def test_complete_download(tmp_path):
    """Moves .part to final location and cleans up."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path / "in_progress"))
    output_dir = tmp_path / "completed"

    meta = PartFileMetadata(
        job_id="test-123",
        url="https://test.com",
        provider="youtube",
        filename="Video.mp4",
        media_type="video",
        output_directory=str(output_dir),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    part_path = mgr.create_part_file("test-123", meta)
    part_path.write_bytes(b"video data here")

    final = mgr.complete_download("test-123", "Video.mp4", str(output_dir))

    assert final.exists()
    assert final.name == "Video.mp4"
    assert final.read_bytes() == b"video data here"
    assert not part_path.exists()


def test_complete_download_filename_conflict(tmp_path):
    """Handles existing file by adding counter."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path / "in_progress"))
    output_dir = tmp_path / "completed"
    output_dir.mkdir()

    (output_dir / "Video.mp4").write_bytes(b"existing")

    meta = PartFileMetadata(
        job_id="test-456",
        url="https://test.com",
        provider="youtube",
        filename="Video.mp4",
        media_type="video",
        output_directory=str(output_dir),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    part_path = mgr.create_part_file("job-456", meta)
    part_path.write_bytes(b"new data")

    final = mgr.complete_download("job-456", "Video.mp4", str(output_dir))

    assert final.exists()
    assert "Video (1).mp4" in final.name


def test_cleanup(tmp_path):
    """Cleanup removes all files for a job."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path))
    meta = PartFileMetadata(
        job_id="test-789",
        url="https://test.com",
        provider="youtube",
        filename="Video.mp4",
        media_type="video",
        output_directory=str(tmp_path),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    mgr.create_part_file("test-789", meta)
    mgr.update_progress(
        "test-789",
        PartFileProgress(
            job_id="test-789",
            last_updated=datetime.now().isoformat(),
        ),
    )

    mgr.cleanup("test-789")

    assert mgr.get_metadata("test-789") is None
    assert mgr.get_progress("test-789") is None
    assert mgr.get_part_file_path("test-789") is None


def test_list_incomplete(tmp_path):
    """Lists all incomplete downloads."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path))

    for i in range(3):
        meta = PartFileMetadata(
            job_id=f"job-{i}",
            url=f"https://test.com/v{i}",
            provider="youtube",
            filename=f"Video{i}.mp4",
            media_type="video",
            output_directory=str(tmp_path),
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        path = mgr.create_part_file(f"job-{i}", meta)
        path.write_bytes(b"x" * (i + 1) * 1000)

    incomplete = mgr.list_incomplete_downloads()
    assert len(incomplete) == 3


def test_get_metadata_nonexistent(tmp_path):
    """Returns None for nonexistent metadata."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path))
    assert mgr.get_metadata("nonexistent") is None


def test_get_progress_nonexistent(tmp_path):
    """Returns None for nonexistent progress."""
    mgr = PartFileManager(in_progress_dir=str(tmp_path))
    assert mgr.get_progress("nonexistent") is None

