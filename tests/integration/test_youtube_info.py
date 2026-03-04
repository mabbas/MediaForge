"""Integration tests for YouTube info extraction."""

from __future__ import annotations

import pytest

from src.exceptions import ProviderError
from src.models.enums import ProviderType

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


def test_extract_info_real(youtube_provider, test_video_url, test_video_id) -> None:
    """Extract real video metadata from YouTube."""
    try:
        info = youtube_provider.extract_info(test_video_url)
    except ProviderError as exc:
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            pytest.skip(f"YouTube test video not accessible: {msg}")
        raise

    assert info.provider == ProviderType.YOUTUBE
    assert info.media_id == test_video_id
    assert info.title
    assert info.duration_seconds is not None
    assert info.duration_seconds > 0
    assert len(info.formats) > 0

    print(f"\n  Title: {info.title}")
    print(f"  Duration: {info.duration_human}")
    print(f"  Formats: {len(info.formats)}")
    print(f"  Channel: {info.channel_name}")
    print(f"  Thumbnails: {len(info.thumbnails)}")

    subs = list(info.subtitles_available.keys())
    print(f"  Subtitles: {subs[:5]}")


def test_get_formats_real(youtube_provider, test_video_url) -> None:
    """Get real format list from YouTube."""
    try:
        formats = youtube_provider.get_formats(test_video_url)
    except ProviderError as exc:
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            pytest.skip(f"YouTube test video not accessible: {msg}")
        raise

    assert len(formats) > 0

    video_formats = [f for f in formats if f.has_video]
    assert len(video_formats) > 0

    audio_formats = [f for f in formats if f.has_audio and not f.has_video]
    assert len(audio_formats) > 0

    extensions = {f.extension for f in formats}
    print(f"\n  Total formats: {len(formats)}")
    print(f"  Video formats: {len(video_formats)}")
    print(f"  Audio formats: {len(audio_formats)}")
    print(f"  Extensions: {extensions}")


def test_extract_info_formats_have_data(youtube_provider, test_video_url) -> None:
    """Verify extracted formats have meaningful data."""
    try:
        info = youtube_provider.extract_info(test_video_url)
    except ProviderError as exc:
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            pytest.skip(f"YouTube test video not accessible: {msg}")
        raise

    checked = 0
    for fmt in info.formats[:5]:
        # Some formats (e.g. storyboards) may have neither audio nor video streams.
        if not (fmt.has_video or fmt.has_audio):
            continue
        assert fmt.format_id
        assert fmt.extension
        assert fmt.has_video or fmt.has_audio
        checked += 1

    assert checked > 0


def test_extract_info_thumbnails(youtube_provider, test_video_url) -> None:
    """Verify thumbnails are extracted."""
    try:
        info = youtube_provider.extract_info(test_video_url)
    except ProviderError as exc:
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            pytest.skip(f"YouTube test video not accessible: {msg}")
        raise

    assert len(info.thumbnails) > 0
    best = info.best_thumbnail
    assert best is not None
    assert best.url.startswith("http")
    print(f"\n  Best thumbnail: {best.width}x{best.height}")


def test_extract_info_invalid_url(youtube_provider) -> None:
    """Invalid video URL raises ProviderError."""
    with pytest.raises(ProviderError) as exc_info:
        youtube_provider.extract_info("https://youtube.com/watch?v=INVALID_NOT_EXIST_XYZ123")
    assert "YouTube" in str(exc_info.value)
    print(f"\n  Error: {exc_info.value}")


def test_extract_info_metadata_fields(youtube_provider, test_video_url) -> None:
    """Key metadata fields populated."""
    try:
        info = youtube_provider.extract_info(test_video_url)
    except ProviderError as exc:
        msg = str(exc)
        if "Video unavailable" in msg or "Private video" in msg:
            pytest.skip(f"YouTube test video not accessible: {msg}")
        raise

    assert info.url
    assert info.title
    assert info.media_id
    assert info.is_live is False
    if info.view_count is not None:
        assert info.view_count > 0

