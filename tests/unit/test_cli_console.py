"""Tests for GrabItDown CLI console utilities."""

from src.cli.console import format_duration, format_size


def test_format_size_bytes():
    """Small value formatted as bytes."""
    assert format_size(500) == "500 B"


def test_format_size_kb():
    """KB range formatted correctly."""
    result = format_size(5120)
    assert "KB" in result


def test_format_size_mb():
    """MB range formatted correctly."""
    result = format_size(5242880)
    assert "MB" in result


def test_format_size_gb():
    """GB range formatted correctly."""
    result = format_size(1073741824)
    assert result == "1.00 GB"


def test_format_size_none():
    """None returns Unknown."""
    assert format_size(None) == "Unknown"


def test_format_duration_seconds():
    """Short duration."""
    assert format_duration(45) == "45s"


def test_format_duration_minutes():
    """Minutes and seconds."""
    assert format_duration(150) == "2m 30s"


def test_format_duration_hours():
    """Hours, minutes, seconds."""
    assert format_duration(5025) == "1h 23m 45s"


def test_format_duration_none():
    """None returns Unknown."""
    assert format_duration(None) == "Unknown"
