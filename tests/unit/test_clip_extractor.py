"""Tests for clip extraction."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src.clip.extractor import (
    ClipExtractor,
    ClipRequest,
    get_clip_extractor,
    reset_clip_extractor,
)


@pytest.fixture
def extractor(tmp_path):
    return ClipExtractor(output_dir=str(tmp_path))


@pytest.fixture
def sample_video(tmp_path):
    """Create a 10-second test video using FFmpeg."""
    video_path = str(tmp_path / "test_video.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=10:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        pytest.skip("FFmpeg not available or failed")
    return video_path


def test_timestamp_seconds(extractor):
    assert extractor._timestamp_to_seconds("90") == 90.0
    assert extractor._timestamp_to_seconds("0") == 0.0
    assert extractor._timestamp_to_seconds("5.5") == 5.5


def test_timestamp_mmss(extractor):
    assert extractor._timestamp_to_seconds("1:30") == 90.0
    assert extractor._timestamp_to_seconds("0:45") == 45.0


def test_timestamp_hhmmss(extractor):
    assert extractor._timestamp_to_seconds("1:30:00") == 5400.0
    assert extractor._timestamp_to_seconds("0:01:30") == 90.0
    assert extractor._timestamp_to_seconds("0:00:45.500") == 45.5


def test_timestamp_invalid(extractor):
    with pytest.raises(ValueError):
        extractor._timestamp_to_seconds("abc")


def test_validate_valid(extractor):
    valid, err = extractor.validate_timestamps("00:01:00", "00:02:00")
    assert valid is True
    assert err == ""


def test_validate_end_before_start(extractor):
    valid, err = extractor.validate_timestamps("00:02:00", "00:01:00")
    assert valid is False
    assert "after" in err.lower()


def test_validate_equal_times(extractor):
    valid, err = extractor.validate_timestamps("00:01:00", "00:01:00")
    assert valid is False


def test_validate_too_short(extractor):
    valid, err = extractor.validate_timestamps("0", "0.1")
    assert valid is False
    assert "0.5" in err


def test_validate_too_long(extractor):
    valid, err = extractor.validate_timestamps("0", "7201")
    assert valid is False
    assert "2 hours" in err


def test_validate_exceeds_duration(extractor):
    valid, err = extractor.validate_timestamps("0", "15", source_duration=10.0)
    assert valid is False
    assert "exceeds" in err.lower()


def test_validate_negative(extractor):
    valid, err = extractor.validate_timestamps("-1", "5")
    assert valid is False


def test_extract_precise(extractor, sample_video):
    req = ClipRequest(source=sample_video, start_time="2", end_time="5", mode="precise")
    result = extractor.extract(req)
    assert result.success is True
    assert os.path.exists(result.output_path)
    assert result.file_size_bytes > 0
    assert result.duration_seconds > 0


def test_extract_fast(extractor, sample_video):
    req = ClipRequest(source=sample_video, start_time="1", end_time="8", mode="fast")
    result = extractor.extract(req)
    assert result.success is True
    assert os.path.exists(result.output_path)


def test_extract_hhmmss_format(extractor, sample_video):
    req = ClipRequest(
        source=sample_video,
        start_time="00:00:01",
        end_time="00:00:05",
        mode="precise",
    )
    result = extractor.extract(req)
    assert result.success is True


def test_extract_nonexistent_source(extractor):
    req = ClipRequest(
        source="/nonexistent/video.mp4",
        start_time="0",
        end_time="5",
    )
    result = extractor.extract(req)
    assert result.success is False
    assert "not found" in (result.error or "").lower()


def test_extract_invalid_times(extractor, sample_video):
    req = ClipRequest(source=sample_video, start_time="5", end_time="2")
    result = extractor.extract(req)
    assert result.success is False


def test_singleton():
    reset_clip_extractor()
    e1 = get_clip_extractor()
    e2 = get_clip_extractor()
    assert e1 is e2
    reset_clip_extractor()
    e3 = get_clip_extractor()
    assert e3 is not e1


def test_calc_duration(extractor):
    assert extractor._calc_duration("0:00", "1:30") == "90.0"
    assert extractor._calc_duration("1:00", "2:30") == "90.0"


def test_ffmpeg_cmd_precise(extractor):
    req = ClipRequest(
        source="/test.mp4", start_time="10", end_time="20", mode="precise"
    )
    cmd = extractor._build_ffmpeg_cmd("/test.mp4", "/out.mp4", req)
    assert "-ss" in cmd
    assert "-to" in cmd
    assert "libx264" in cmd
    assert "copy" not in cmd


def test_ffmpeg_cmd_fast(extractor):
    req = ClipRequest(
        source="/test.mp4", start_time="10", end_time="20", mode="fast"
    )
    cmd = extractor._build_ffmpeg_cmd("/test.mp4", "/out.mp4", req)
    assert "copy" in cmd
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < i_idx
