"""Tests for GrabItDown YouTube provider."""

from src.models.download import DownloadRequest
from src.models.enums import (
    AudioFormat,
    MediaType,
    ProviderType,
    Quality,
    VideoFormat,
)
from src.providers.youtube.provider import YouTubeProvider


def test_youtube_name() -> None:
    """Provider name is 'YouTube'."""
    p = YouTubeProvider()
    assert p.name == "YouTube"


def test_youtube_provider_type() -> None:
    """Provider type is YOUTUBE."""
    p = YouTubeProvider()
    assert p.provider_type == ProviderType.YOUTUBE


def test_youtube_capabilities() -> None:
    """All capability flags correct."""
    p = YouTubeProvider()
    caps = p.capabilities
    assert caps.supports_video is True
    assert caps.supports_audio is True
    assert caps.supports_playlists is True
    assert caps.supports_subtitles is True
    assert caps.supports_live_streams is True
    assert caps.supports_resume is True
    assert caps.max_quality == Quality.Q_2160P


def test_youtube_can_handle_standard_url() -> None:
    """Handles youtube.com/watch URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True


def test_youtube_can_handle_short_url() -> None:
    """Handles youtu.be short URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://youtu.be/dQw4w9WgXcQ") is True


def test_youtube_can_handle_mobile_url() -> None:
    """Handles m.youtube.com URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://m.youtube.com/watch?v=test") is True


def test_youtube_can_handle_music_url() -> None:
    """Handles music.youtube.com URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://music.youtube.com/watch?v=test") is True


def test_youtube_can_handle_shorts_url() -> None:
    """Handles youtube.com/shorts URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://www.youtube.com/shorts/abc123") is True


def test_youtube_can_handle_playlist_url() -> None:
    """Handles playlist URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://www.youtube.com/playlist?list=PLtest") is True


def test_youtube_cannot_handle_vimeo() -> None:
    """Rejects vimeo.com URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://vimeo.com/123456") is False


def test_youtube_cannot_handle_facebook() -> None:
    """Rejects facebook.com URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://facebook.com/video/123") is False


def test_youtube_cannot_handle_random() -> None:
    """Rejects random URLs."""
    p = YouTubeProvider()
    assert p.can_handle("https://example.com/video") is False


def test_youtube_build_format_string_1080p() -> None:
    """1080p format string correct."""
    p = YouTubeProvider()
    fmt = p._build_format_string(Quality.Q_1080P)
    assert "1080" in fmt
    assert "bestvideo" in fmt
    assert "bestaudio" in fmt


def test_youtube_build_format_string_best() -> None:
    """BEST quality format string."""
    p = YouTubeProvider()
    fmt = p._build_format_string(Quality.BEST)
    assert "bestvideo+bestaudio" in fmt


def test_youtube_build_format_string_worst() -> None:
    """WORST quality format string."""
    p = YouTubeProvider()
    fmt = p._build_format_string(Quality.WORST)
    assert "worst" in fmt


def test_youtube_map_format_video() -> None:
    """Maps video format dict correctly."""
    p = YouTubeProvider()
    fmt_dict = {
        "format_id": "137",
        "ext": "mp4",
        "format_note": "1080p",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "vcodec": "avc1.640028",
        "acodec": "none",
        "filesize": 52428800,
        "tbr": 4000.0,
        "format": "137 - 1920x1080 (1080p)",
    }
    result = p._map_format(fmt_dict)
    assert result.format_id == "137"
    assert result.extension == "mp4"
    assert result.resolution == "1920x1080"
    assert result.has_video is True
    assert result.has_audio is False
    assert result.is_video_only is True
    assert result.filesize_bytes == 52428800


def test_youtube_map_format_audio() -> None:
    """Maps audio format dict correctly."""
    p = YouTubeProvider()
    fmt_dict = {
        "format_id": "140",
        "ext": "m4a",
        "format_note": "medium",
        "vcodec": "none",
        "acodec": "mp4a.40.2",
        "tbr": 128.0,
    }
    result = p._map_format(fmt_dict)
    assert result.format_id == "140"
    assert result.has_video is False
    assert result.has_audio is True
    assert result.is_audio_only is True


def test_youtube_map_info_to_media() -> None:
    """Maps full info_dict to MediaInfo."""
    p = YouTubeProvider()
    info_dict = {
        "id": "dQw4w9WgXcQ",
        "title": "Test Video",
        "description": "A test video",
        "duration": 212,
        "channel": "Test Channel",
        "channel_url": "https://youtube.com/c/test",
        "upload_date": "20240101",
        "view_count": 1000000,
        "like_count": 50000,
        "webpage_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "is_live": False,
        "thumbnails": [
            {"url": "http://img/1.jpg", "width": 120, "height": 90},
            {"url": "http://img/2.jpg", "width": 1280, "height": 720, "id": "maxres"},
        ],
        "formats": [
            {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "width": 1920, "height": 1080},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
        ],
        "subtitles": {
            "en": [{"ext": "srt"}, {"ext": "vtt"}],
        },
        "automatic_captions": {
            "ur": [{"ext": "vtt"}],
        },
    }
    result = p._map_info_to_media(info_dict)
    assert result.media_id == "dQw4w9WgXcQ"
    assert result.title == "Test Video"
    assert result.duration_seconds == 212
    assert result.provider == ProviderType.YOUTUBE
    assert len(result.thumbnails) == 2
    assert len(result.formats) == 2
    assert result.has_subtitles("en") is True
    assert result.has_subtitles("ur") is True
    assert result.has_subtitles("fr") is False
    assert result.channel_name == "Test Channel"
    assert result.is_live is False


def test_youtube_build_download_opts_audio() -> None:
    """Audio download options configured correctly."""
    p = YouTubeProvider()
    req = DownloadRequest(
        url="https://youtube.com/watch?v=test",
        media_type=MediaType.AUDIO,
        audio_format=AudioFormat.MP3,
        audio_bitrate="320k",
    )
    opts = p._build_download_opts(req, "/tmp/test", None)
    assert opts["format"] == "bestaudio/best"
    assert any(pp["key"] == "FFmpegExtractAudio" for pp in opts.get("postprocessors", []))


def test_youtube_build_download_opts_video() -> None:
    """Video download options configured correctly."""
    p = YouTubeProvider()
    req = DownloadRequest(
        url="https://youtube.com/watch?v=test",
        media_type=MediaType.VIDEO,
        quality=Quality.Q_720P,
        video_format=VideoFormat.MP4,
    )
    opts = p._build_download_opts(req, "/tmp/test", None)
    assert "720" in opts["format"]
    assert opts["merge_output_format"] == "mp4"


def test_youtube_build_download_opts_with_subtitles() -> None:
    """Subtitle options included when requested."""
    p = YouTubeProvider()
    req = DownloadRequest(
        url="https://youtube.com/watch?v=test",
        embed_subtitles=True,
        subtitle_languages=["en", "ur"],
    )
    opts = p._build_download_opts(req, "/tmp/test", None)
    assert opts.get("writesubtitles") is True
    assert opts.get("subtitleslangs") == ["en", "ur"]


def test_youtube_sanitize_filename_inherited() -> None:
    """Inherited sanitize_filename works."""
    p = YouTubeProvider()
    result = p.sanitize_filename('Video: "Test" File/Name')
    assert ":" not in result
    assert '"' not in result
    assert "/" not in result

