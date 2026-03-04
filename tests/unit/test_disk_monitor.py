"""Tests for GrabItDown disk monitor."""

import os
import tempfile

from src.download.disk_monitor import DiskMonitor


def test_get_free_space(tmp_path):
    """Returns positive free space."""
    monitor = DiskMonitor(download_dir=str(tmp_path), min_space_mb=10)
    free = monitor.get_free_space_mb()
    assert free > 0


def test_has_enough_space(tmp_path):
    """Has enough space for small file."""
    monitor = DiskMonitor(download_dir=str(tmp_path), min_space_mb=10)
    assert monitor.has_enough_space(1024) is True


def test_check_before_download(tmp_path):
    """Pre-download check passes for small file."""
    monitor = DiskMonitor(download_dir=str(tmp_path), min_space_mb=10)
    can_proceed, reason = monitor.check_before_download(
        estimated_size_bytes=1048576,
    )
    assert can_proceed is True
    assert "OK" in reason


def test_get_stats(tmp_path):
    """Stats contain expected fields."""
    monitor = DiskMonitor(download_dir=str(tmp_path), min_space_mb=500)
    stats = monitor.get_stats()
    assert "total_gb" in stats
    assert "free_gb" in stats
    assert "usage_percent" in stats
    assert "min_space_mb" in stats


def test_creates_directory():
    """Creates download directory if needed."""
    new_dir = os.path.join(tempfile.mkdtemp(), "new_subdir")
    monitor = DiskMonitor(download_dir=new_dir, min_space_mb=10)
    assert os.path.isdir(new_dir)
