"""Integration test configuration and fixtures."""

from __future__ import annotations

import socket

import pytest

from src.providers.youtube.playlist import YouTubePlaylistHandler
from src.providers.youtube.provider import YouTubeProvider

# Test video: public, stable, relatively short. If this
# becomes unavailable, replace with another small,
# public YouTube clip.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=3hakaoeakiI"
TEST_VIDEO_ID = "3hakaoeakiI"

TEST_PLAYLIST_URL = (
    "https://www.youtube.com/playlist?list=PL9pQ_kOl1AUs2Hzq7GTEYfJUlUElhxF6R"
)


def has_internet() -> bool:
    """Check if we have internet connectivity."""
    try:
        socket.create_connection(("www.youtube.com", 443), timeout=5)
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not has_internet(), reason="No internet connection available")


@pytest.fixture
def youtube_provider() -> YouTubeProvider:
    """Fresh YouTubeProvider instance."""
    return YouTubeProvider()


@pytest.fixture
def playlist_handler(youtube_provider: YouTubeProvider) -> YouTubePlaylistHandler:
    """YouTubePlaylistHandler instance."""
    return YouTubePlaylistHandler(youtube_provider)


@pytest.fixture
def test_video_url() -> str:
    """Test video URL."""
    return TEST_VIDEO_URL


@pytest.fixture
def test_video_id() -> str:
    """Test video ID."""
    return TEST_VIDEO_ID


@pytest.fixture
def test_playlist_url() -> str:
    """Test playlist URL."""
    return TEST_PLAYLIST_URL

