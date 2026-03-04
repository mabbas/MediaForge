"""Tests for GrabItDown playlist models."""

import pytest
from pydantic import ValidationError

from src.models.enums import ProviderType
from src.models.playlist import PlaylistDownloadRequest, PlaylistInfo, PlaylistItem


def test_playlist_item_duration_human() -> None:
    """Duration formatted correctly."""
    item = PlaylistItem(
        index=1,
        url="https://test.com",
        title="Test",
        media_id="1",
        duration_seconds=3661,
    )
    assert item.duration_human == "1h 1m 1s"


def test_playlist_item_duration_none() -> None:
    """None duration shows 'Unknown'."""
    item = PlaylistItem(index=1, url="https://test.com", title="Test", media_id="1")
    assert item.duration_human == "Unknown"


def test_playlist_info_total_duration() -> None:
    """Total duration sums all items."""
    playlist = PlaylistInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        playlist_id="test",
        title="Test",
        items=[
            PlaylistItem(index=1, url="u1", title="V1", media_id="1", duration_seconds=300),
            PlaylistItem(index=2, url="u2", title="V2", media_id="2", duration_seconds=600),
            PlaylistItem(index=3, url="u3", title="V3", media_id="3", duration_seconds=450),
        ],
    )
    assert playlist.total_duration_seconds == 1350


def test_playlist_info_total_duration_with_none() -> None:
    """Skips items with None duration."""
    playlist = PlaylistInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        playlist_id="test",
        title="Test",
        items=[
            PlaylistItem(index=1, url="u1", title="V1", media_id="1", duration_seconds=300),
            PlaylistItem(index=2, url="u2", title="V2", media_id="2"),
        ],
    )
    assert playlist.total_duration_seconds == 300


def test_playlist_info_available_items() -> None:
    """Filters out unavailable items."""
    playlist = PlaylistInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        playlist_id="test",
        title="Test",
        items=[
            PlaylistItem(index=1, url="u1", title="V1", media_id="1"),
            PlaylistItem(index=2, url="u2", title="V2", media_id="2", is_available=False),
            PlaylistItem(index=3, url="u3", title="V3", media_id="3"),
        ],
    )
    assert len(playlist.available_items) == 2


def test_playlist_info_get_items_by_range() -> None:
    """Slice returns correct items."""
    playlist = PlaylistInfo(
        url="https://test.com",
        provider=ProviderType.YOUTUBE,
        playlist_id="test",
        title="Test",
        items=[
            PlaylistItem(index=i, url=f"u{i}", title=f"V{i}", media_id=str(i))
            for i in range(1, 6)
        ],
    )
    result = playlist.get_items_by_range(1, 3)
    assert len(result) == 2
    assert result[0].index == 2
    assert result[1].index == 3


def test_playlist_download_request_defaults() -> None:
    """Verify default values."""
    req = PlaylistDownloadRequest(url="https://youtube.com/playlist?list=test")
    assert req.items == "all"
    assert req.concurrency == 3
    assert req.skip_existing is True


def test_playlist_download_request_concurrency_valid() -> None:
    """Concurrency 1-5 passes."""
    req = PlaylistDownloadRequest(
        url="https://youtube.com/playlist?list=test",
        concurrency=5,
    )
    assert req.concurrency == 5


def test_playlist_download_request_concurrency_invalid() -> None:
    """Concurrency > 5 fails validation."""
    with pytest.raises(ValidationError):
        PlaylistDownloadRequest(
            url="https://youtube.com/playlist?list=test",
            concurrency=10,
        )


def test_playlist_download_request_concurrency_zero() -> None:
    """Concurrency 0 fails validation."""
    with pytest.raises(ValidationError):
        PlaylistDownloadRequest(
            url="https://youtube.com/playlist?list=test",
            concurrency=0,
        )


def test_playlist_download_request_items_all() -> None:
    """items='all' is valid."""
    req = PlaylistDownloadRequest(
        url="https://youtube.com/playlist?list=test",
        items="all",
    )
    assert req.items == "all"


def test_playlist_download_request_items_list() -> None:
    """items=[1,3,5] is valid."""
    req = PlaylistDownloadRequest(
        url="https://youtube.com/playlist?list=test",
        items=[1, 3, 5],
    )
    assert req.items == [1, 3, 5]


def test_playlist_download_request_items_invalid_string() -> None:
    """items='some' fails (only 'all' allowed)."""
    with pytest.raises(ValidationError):
        PlaylistDownloadRequest(
            url="https://youtube.com/playlist?list=test",
            items="some",
        )


def test_playlist_download_request_url_validation() -> None:
    """Invalid URL fails."""
    with pytest.raises(ValidationError):
        PlaylistDownloadRequest(url="not-a-url")

