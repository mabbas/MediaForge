"""Integration tests for provider auto-detection."""

from __future__ import annotations

import pytest

from src.core.provider_factory import create_provider_registry

pytestmark = [pytest.mark.integration]


def test_detect_youtube_url() -> None:
    """Auto-detects YouTube provider for YouTube URLs."""
    registry = create_provider_registry()

    urls = [
        "https://www.youtube.com/watch?v=3hakaoeakiI",
        "https://youtu.be/3hakaoeakiI",
        "https://m.youtube.com/watch?v=test",
    ]
    for url in urls:
        provider = registry.detect_provider(url)
        assert provider.name == "YouTube", f"Expected YouTube for {url}"
        print(f"  {url[:50]}... → {provider.name}")


def test_detect_unknown_url_falls_to_generic() -> None:
    """Unknown URLs fall back to Generic provider."""
    registry = create_provider_registry()
    provider = registry.detect_provider("https://example.com/some-video")
    assert provider.name == "Generic"
    print(f"  Unknown URL → {provider.name}")


def test_full_flow_detect_and_info() -> None:
    """Full flow: detect provider → extract info."""
    registry = create_provider_registry()
    url = "https://www.youtube.com/watch?v=3hakaoeakiI"

    provider = registry.detect_provider(url)
    assert provider.name == "YouTube"

    info = provider.extract_info(url)
    assert info.title
    assert info.media_id == "3hakaoeakiI"

    print(f"\n  Detected: {provider.name}")
    print(f"  Title: {info.title}")
    print(f"  Formats: {len(info.formats)}")

