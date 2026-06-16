"""Tests for FFmpeg path resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.env_loader import resolve_ffmpeg_directory


@pytest.mark.skipif(not Path("C:/ffmpeg/bin/ffmpeg.exe").is_file(), reason="ffmpeg not at C:/ffmpeg/bin")
def test_resolve_ffmpeg_directory_parent_folder() -> None:
    """C:/ffmpeg should resolve to C:/ffmpeg/bin when binaries live there."""
    resolved = resolve_ffmpeg_directory("C:/ffmpeg")
    assert resolved is not None
    assert resolved.name == "bin"
    assert (resolved / "ffmpeg.exe").is_file()


@pytest.mark.skipif(not Path("C:/ffmpeg/bin/ffmpeg.exe").is_file(), reason="ffmpeg not at C:/ffmpeg/bin")
def test_resolve_ffmpeg_directory_bin_folder() -> None:
    resolved = resolve_ffmpeg_directory("C:/ffmpeg/bin")
    assert resolved is not None
    assert (resolved / "ffmpeg.exe").is_file()


def test_resolve_ffmpeg_directory_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GID_FFMPEG_LOCATION", raising=False)
    assert resolve_ffmpeg_directory("/nonexistent/ffmpeg/path") is None
