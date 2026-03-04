"""Integration tests for YouTube playlist extraction."""

from __future__ import annotations

import pytest

from src.models.enums import ProviderType
from src.exceptions import ProviderError

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]


def test_get_playlist_info_real(playlist_handler, test_playlist_url) -> None:
    """Extract real playlist metadata."""
    info = playlist_handler.get_playlist_info(test_playlist_url)

    assert info.provider == ProviderType.YOUTUBE
    assert info.title
    assert info.playlist_id
    assert len(info.items) > 0
    assert info.item_count > 0

    print(f"\n  Title: {info.title}")
    print(f"  Items: {info.item_count}")
    print(f"  Total duration: {info.total_duration_human}")
    print(f"  Available: {len(info.available_items)}")

    for item in info.items[:3]:
        print(f"    {item.index}. {item.title} ({item.duration_human})")


def test_playlist_items_have_data(playlist_handler, test_playlist_url) -> None:
    """Playlist items have meaningful data."""
    info = playlist_handler.get_playlist_info(test_playlist_url)

    for item in info.items[:5]:
        assert item.url
        assert item.title
        assert item.media_id
        assert item.index > 0


def test_playlist_available_items_filter(playlist_handler, test_playlist_url) -> None:
    """available_items filters correctly."""
    info = playlist_handler.get_playlist_info(test_playlist_url)

    available = info.available_items
    for item in available:
        assert item.is_available is True

    print(f"\n  Total items: {len(info.items)}")
    print(f"  Available: {len(available)}")


def test_playlist_invalid_url(playlist_handler) -> None:
    """Invalid playlist URL raises ProviderError."""
    with pytest.raises(ProviderError):
        playlist_handler.get_playlist_info("https://youtube.com/playlist?list=PLnotexist12345")

