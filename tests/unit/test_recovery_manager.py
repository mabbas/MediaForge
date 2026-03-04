"""Tests for GrabItDown recovery manager."""

from datetime import datetime

from src.resume.part_file_manager import PartFileManager, PartFileMetadata, PartFileProgress
from src.resume.recovery_manager import RecoveryManager


def test_scan_incomplete_empty(tmp_path):
    """Empty directory returns empty list."""
    pfm = PartFileManager(in_progress_dir=str(tmp_path))
    mgr = RecoveryManager(part_file_manager=pfm)

    result = mgr.scan_incomplete()
    assert result == []


def test_scan_incomplete_with_downloads(tmp_path):
    """Finds incomplete downloads with integrity check."""
    pfm = PartFileManager(in_progress_dir=str(tmp_path))

    for i in range(2):
        meta = PartFileMetadata(
            job_id=f"job-{i}",
            url=f"https://test.com/v{i}",
            provider="youtube",
            total_bytes=1_000_000,
            filename=f"Video{i}.mp4",
            media_type="video",
            output_directory=str(tmp_path),
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        path = pfm.create_part_file(f"job-{i}", meta)
        path.write_bytes(b"x" * 500_000)

        pfm.update_progress(
            f"job-{i}",
            PartFileProgress(
                job_id=f"job-{i}",
                bytes_downloaded=500_000,
                total_bytes=1_000_000,
                percent=50.0,
                last_byte_position=499_999,
                last_updated=datetime.now().isoformat(),
            ),
        )

    mgr = RecoveryManager(part_file_manager=pfm)
    result = mgr.scan_incomplete()

    assert len(result) == 2
    for item in result:
        assert item.integrity_valid is True
        assert item.resume_from_byte == 500_000
        assert item.can_resume is True
        assert abs(item.percent_complete - 50.0) < 1.0


def test_scan_incomplete_no_metadata(tmp_path):
    """Downloads without metadata are skipped."""
    pfm = PartFileManager(in_progress_dir=str(tmp_path))

    (tmp_path / "orphan.part").write_bytes(b"x" * 1000)

    mgr = RecoveryManager(part_file_manager=pfm)
    result = mgr.scan_incomplete()
    assert len(result) == 0


def test_recoverable_download_properties(tmp_path):
    """RecoverableDownload properties work correctly."""
    pfm = PartFileManager(in_progress_dir=str(tmp_path))
    meta = PartFileMetadata(
        job_id="test",
        url="https://test.com",
        provider="youtube",
        total_bytes=1_000_000,
        filename="Video.mp4",
        media_type="video",
        output_directory=str(tmp_path),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    path = pfm.create_part_file("test", meta)
    path.write_bytes(b"x" * 635_000)

    mgr = RecoveryManager(part_file_manager=pfm)
    pfm.update_progress(
        "test",
        PartFileProgress(
            job_id="test",
            bytes_downloaded=635_000,
            last_byte_position=634_999,
            last_updated=datetime.now().isoformat(),
        ),
    )

    results = mgr.scan_incomplete()
    assert len(results) == 1

    download = results[0]
    assert download.can_resume is True
    assert abs(download.percent_complete - 63.5) < 0.1


def test_cleanup_download(tmp_path):
    """Cleanup removes a specific download."""
    pfm = PartFileManager(in_progress_dir=str(tmp_path))
    meta = PartFileMetadata(
        job_id="cleanup-test",
        url="https://test.com",
        provider="youtube",
        filename="Video.mp4",
        media_type="video",
        output_directory=str(tmp_path),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    pfm.create_part_file("cleanup-test", meta)

    mgr = RecoveryManager(part_file_manager=pfm)
    mgr.cleanup_download("cleanup-test")

    assert pfm.get_metadata("cleanup-test") is None

