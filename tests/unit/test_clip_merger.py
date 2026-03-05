"""Tests for clip merger."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src.clip.merger import (
    ClipMerger,
    MergeRequest,
    get_clip_merger,
    reset_clip_merger,
)


@pytest.fixture
def merger(tmp_path):
    return ClipMerger(output_dir=str(tmp_path))


@pytest.fixture
def sample_clips(tmp_path):
    """Create two 5-second test video clips."""
    clips = []
    for i in range(2):
        path = str(tmp_path / f"clip_{i}.mp4")
        freq = 440 + i * 220
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=30",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=5",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            pytest.skip("FFmpeg not available")
        clips.append(path)
    return clips


@pytest.fixture
def three_clips(tmp_path):
    """Create three test clips."""
    clips = []
    for i in range(3):
        path = str(tmp_path / f"clip3_{i}.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=30",
            "-f", "lavfi", "-i", f"sine=frequency={440+i*100}:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            pytest.skip("FFmpeg not available")
        clips.append(path)
    return clips


def test_validate_too_few(merger):
    with pytest.raises(ValueError, match="At least 2"):
        merger._validate_clips(["/one.mp4"])


def test_validate_too_many(merger):
    clips = [f"/clip_{i}.mp4" for i in range(25)]
    with pytest.raises(ValueError, match="Maximum 20"):
        merger._validate_clips(clips)


def test_validate_nonexistent(merger):
    with pytest.raises(FileNotFoundError):
        merger._validate_clips([
            "/nonexistent1.mp4",
            "/nonexistent2.mp4",
        ])


def test_merge_two_clips_concat(merger, sample_clips):
    req = MergeRequest(clips=sample_clips, mode="concat")
    result = merger.merge(req)
    assert result.success is True
    assert os.path.exists(result.output_path)
    assert result.file_size_bytes > 0
    assert result.total_duration_seconds >= 8
    assert result.clip_count == 2


def test_merge_two_clips_reencode(merger, sample_clips):
    req = MergeRequest(clips=sample_clips, mode="reencode")
    result = merger.merge(req)
    assert result.success is True
    assert os.path.exists(result.output_path)
    assert result.clip_count == 2


def test_merge_three_clips(merger, three_clips):
    req = MergeRequest(clips=three_clips, mode="concat")
    result = merger.merge(req)
    assert result.success is True
    assert result.clip_count == 3
    assert result.total_duration_seconds >= 7


def test_merge_auto_detect(merger, sample_clips):
    req = MergeRequest(clips=sample_clips, mode="auto")
    result = merger.merge(req)
    assert result.success is True


def test_merge_custom_format(merger, sample_clips):
    req = MergeRequest(
        clips=sample_clips,
        output_format="mkv",
        mode="concat",
    )
    result = merger.merge(req)
    assert result.success is True
    assert result.output_path.endswith(".mkv")


def test_merge_nonexistent_clip(merger, sample_clips):
    req = MergeRequest(
        clips=[sample_clips[0], "/nonexistent.mp4"],
    )
    result = merger.merge(req)
    assert result.success is False
    assert "not found" in (result.error or "").lower()


def test_detect_strategy_same_codec(merger, sample_clips):
    strategy = merger._detect_strategy(sample_clips)
    assert strategy == "concat"


def test_merger_singleton():
    reset_clip_merger()
    m1 = get_clip_merger()
    m2 = get_clip_merger()
    assert m1 is m2
    reset_clip_merger()
    m3 = get_clip_merger()
    assert m3 is not m1
