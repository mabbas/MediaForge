"""Tests for clipboard monitor."""

from __future__ import annotations

from desktop.clipboard import ClipboardMonitor, is_media_url


def test_is_media_url_youtube() -> None:
    assert is_media_url("https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_media_url("https://www.youtube.com/watch?v=abc")
    assert is_media_url("https://youtu.be/dQw4w9WgXcQ")


def test_is_media_url_youtube_playlist() -> None:
    assert is_media_url("https://youtube.com/playlist?list=PLxxxx")


def test_is_media_url_youtube_shorts() -> None:
    assert is_media_url("https://youtube.com/shorts/abc123")


def test_is_media_url_vimeo() -> None:
    assert is_media_url("https://vimeo.com/123456789")


def test_is_media_url_twitter() -> None:
    assert is_media_url("https://twitter.com/user/status/123456")
    assert is_media_url("https://x.com/user/status/123456")


def test_is_media_url_tiktok() -> None:
    assert is_media_url("https://tiktok.com/@user/video/123456")


def test_is_media_url_invalid() -> None:
    assert not is_media_url("")
    assert not is_media_url("not a url")
    assert not is_media_url("https://google.com")
    assert not is_media_url("https://example.com/page")


def test_clipboard_monitor_init() -> None:
    monitor = ClipboardMonitor()
    assert monitor.enabled is True
    assert not monitor._running


def test_clipboard_monitor_enable_disable() -> None:
    monitor = ClipboardMonitor()
    monitor.enabled = False
    assert monitor.enabled is False
    monitor.enabled = True
    assert monitor.enabled is True


def test_clipboard_monitor_clear_history() -> None:
    monitor = ClipboardMonitor()
    monitor._seen_urls.add("https://test.com")
    monitor.clear_history()
    assert len(monitor._seen_urls) == 0
