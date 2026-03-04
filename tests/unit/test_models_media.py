"""Tests for GrabItDown media models."""

from src.models.enums import ProviderType, Quality
from src.models.media import MediaFormat, MediaInfo, Thumbnail


def test_thumbnail_creation() -> None:
    """Basic Thumbnail creation with all fields."""
    t = Thumbnail(url="http://img.jpg", width=1280, height=720, quality="high")
    assert t.width == 1280


def test_media_format_filesize_human_gb() -> None:
    """1GB file displays as '1.00 GB'."""
    fmt = MediaFormat(format_id="137", extension="mp4", filesize_bytes=1073741824)
    assert fmt.filesize_human == "1.00 GB"


def test_media_format_filesize_human_mb() -> None:
    """500MB file displays correctly."""
    fmt = MediaFormat(format_id="22", extension="mp4", filesize_bytes=524288000)
    assert "MB" in fmt.filesize_human


def test_media_format_filesize_human_none() -> None:
    """None filesize shows 'Unknown'."""
    fmt = MediaFormat(format_id="22", extension="mp4")
    assert fmt.filesize_human == "Unknown"


def test_media_format_filesize_approx_fallback() -> None:
    """Falls back to approx filesize when exact is None."""
    fmt = MediaFormat(format_id="22", extension="mp4", filesize_approx_bytes=1048576)
    assert "MB" in fmt.filesize_human or "KB" in fmt.filesize_human


def test_media_format_video_only() -> None:
    """Format with video but no audio is video_only."""
    fmt = MediaFormat(format_id="137", extension="mp4", has_video=True, has_audio=False)
    assert fmt.is_video_only is True
    assert fmt.is_audio_only is False


def test_media_format_audio_only() -> None:
    """Format with audio but no video is audio_only."""
    fmt = MediaFormat(format_id="140", extension="m4a", has_video=False, has_audio=True)
    assert fmt.is_audio_only is True
    assert fmt.is_video_only is False


def test_media_format_both_streams() -> None:
    """Format with both streams is neither _only."""
    fmt = MediaFormat(format_id="22", extension="mp4", has_video=True, has_audio=True)
    assert fmt.is_video_only is False
    assert fmt.is_audio_only is False


def test_media_info_duration_human_hours() -> None:
    """5025 seconds → '1h 23m 45s'."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        duration_seconds=5025,
    )
    assert info.duration_human == "1h 23m 45s"


def test_media_info_duration_human_minutes() -> None:
    """150 seconds → '2m 30s'."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        duration_seconds=150,
    )
    assert info.duration_human == "2m 30s"


def test_media_info_duration_human_seconds() -> None:
    """45 seconds → '45s'."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        duration_seconds=45,
    )
    assert info.duration_human == "45s"


def test_media_info_duration_human_none() -> None:
    """None duration → 'Unknown'."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
    )
    assert info.duration_human == "Unknown"


def test_media_info_best_thumbnail() -> None:
    """Returns largest thumbnail by area."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        thumbnails=[
            Thumbnail(url="small.jpg", width=120, height=90),
            Thumbnail(url="large.jpg", width=1280, height=720),
            Thumbnail(url="med.jpg", width=640, height=480),
        ],
    )
    assert info.best_thumbnail is not None
    assert info.best_thumbnail.url == "large.jpg"


def test_media_info_best_thumbnail_no_dimensions() -> None:
    """Without dimensions, returns last thumbnail."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        thumbnails=[
            Thumbnail(url="first.jpg"),
            Thumbnail(url="last.jpg"),
        ],
    )
    assert info.best_thumbnail is not None
    assert info.best_thumbnail.url == "last.jpg"


def test_media_info_best_thumbnail_empty() -> None:
    """Empty thumbnail list returns None."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
    )
    assert info.best_thumbnail is None


def test_media_info_has_subtitles_true() -> None:
    """Returns True when language exists."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        subtitles_available={"en": ["srt", "vtt"], "ur": ["srt"]},
    )
    assert info.has_subtitles("en") is True
    assert info.has_subtitles("ur") is True


def test_media_info_has_subtitles_false() -> None:
    """Returns False when language doesn't exist."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        subtitles_available={"en": ["srt"]},
    )
    assert info.has_subtitles("fr") is False


def test_media_info_get_audio_formats() -> None:
    """Filters to audio-only formats."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        formats=[
            MediaFormat(format_id="137", extension="mp4", has_video=True, has_audio=False),
            MediaFormat(format_id="140", extension="m4a", has_video=False, has_audio=True),
            MediaFormat(format_id="22", extension="mp4", has_video=True, has_audio=True),
        ],
    )
    audio = info.get_audio_formats()
    assert len(audio) == 1
    assert audio[0].format_id == "140"


def test_media_info_get_video_formats() -> None:
    """Filters to formats with video."""
    info = MediaInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        media_id="test",
        title="Test",
        formats=[
            MediaFormat(format_id="137", extension="mp4", has_video=True, has_audio=False),
            MediaFormat(format_id="140", extension="m4a", has_video=False, has_audio=True),
            MediaFormat(format_id="22", extension="mp4", has_video=True, has_audio=True),
        ],
    )
    video = info.get_video_formats()
    assert len(video) == 2

