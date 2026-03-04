"""Tests for GrabItDown provider factory."""

from src.core.provider_factory import create_provider_registry
from src.models.enums import ProviderType


def test_create_registry() -> None:
    """Factory creates registry with default providers."""
    registry = create_provider_registry()
    assert registry.media_provider_count >= 2
    assert registry.is_registered(ProviderType.YOUTUBE) is True
    assert registry.is_registered(ProviderType.GENERIC) is True


def test_factory_youtube_provider() -> None:
    """YouTube provider accessible from factory registry."""
    registry = create_provider_registry()
    yt = registry.get_provider(ProviderType.YOUTUBE)
    assert yt.name == "YouTube"


def test_factory_generic_provider() -> None:
    """Generic provider accessible from factory registry."""
    registry = create_provider_registry()
    gen = registry.get_provider(ProviderType.GENERIC)
    assert gen.name == "Generic"


def test_factory_detection_youtube() -> None:
    """Factory registry detects YouTube URLs."""
    registry = create_provider_registry()
    detected = registry.detect_provider("https://www.youtube.com/watch?v=test")
    assert detected.name == "YouTube"


def test_factory_detection_fallback() -> None:
    """Factory registry falls back to Generic."""
    registry = create_provider_registry()
    detected = registry.detect_provider("https://random-site.com/video")
    assert detected.name == "Generic"


def test_factory_lists_all() -> None:
    """Factory registry lists all providers."""
    registry = create_provider_registry()
    providers = registry.list_providers()
    names = [p["name"] for p in providers]
    assert "YouTube" in names
    assert "Generic" in names

