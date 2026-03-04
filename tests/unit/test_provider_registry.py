"""Tests for GrabItDown provider registry."""

import pytest

from src.core.base_provider import BaseMediaProvider
from src.core.interfaces import ProviderCapabilities
from src.core.provider_registry import ProviderRegistry
from src.exceptions import ProviderError
from src.models.download import DownloadRequest, DownloadResult
from src.models.enums import ProviderType
from src.models.media import MediaFormat, MediaInfo


class MockProvider(BaseMediaProvider):
    """Mock provider that only handles mock.com URLs."""

    @property
    def name(self) -> str:
        """Return mock provider name."""
        return "MockProvider"

    @property
    def provider_type(self) -> ProviderType:
        """Return mock provider type."""
        return ProviderType.YOUTUBE

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return capabilities restricted to mock.com."""
        return ProviderCapabilities(supported_domains=["mock.com"])

    def can_handle(self, url: str) -> bool:
        """Use validate_url for handling logic."""
        return self.validate_url(url)

    def extract_info(self, url: str) -> MediaInfo:
        """Not implemented for tests."""
        raise NotImplementedError

    def get_formats(self, url: str) -> list[MediaFormat]:
        """Not implemented for tests."""
        raise NotImplementedError

    def download(
        self,
        request: DownloadRequest,
        output_dir: str | None = None,
        progress_callback=None,
    ) -> DownloadResult:
        """Not implemented for tests."""
        raise NotImplementedError


class MockOpenProvider(BaseMediaProvider):
    """Mock provider that accepts any valid HTTP URL."""

    @property
    def name(self) -> str:
        """Return open mock provider name."""
        return "MockOpenProvider"

    @property
    def provider_type(self) -> ProviderType:
        """Return generic provider type."""
        return ProviderType.GENERIC

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return capabilities with no domain restriction."""
        return ProviderCapabilities(supported_domains=[])

    def can_handle(self, url: str) -> bool:
        """Use validate_url for handling logic."""
        return self.validate_url(url)

    def extract_info(self, url: str) -> MediaInfo:
        """Not implemented for tests."""
        raise NotImplementedError

    def get_formats(self, url: str) -> list[MediaFormat]:
        """Not implemented for tests."""
        raise NotImplementedError

    def download(
        self,
        request: DownloadRequest,
        output_dir: str | None = None,
        progress_callback=None,
    ) -> DownloadResult:
        """Not implemented for tests."""
        raise NotImplementedError


def test_register_and_get_provider() -> None:
    """Register provider and retrieve by type."""
    registry = ProviderRegistry()
    mock = MockProvider()
    registry.register(mock)
    result = registry.get_provider(mock.provider_type)
    assert result.name == mock.name


def test_get_unregistered_provider() -> None:
    """Getting unregistered provider raises ProviderError."""
    registry = ProviderRegistry()
    with pytest.raises(ProviderError):
        registry.get_provider(ProviderType.FACEBOOK)


def test_detect_provider_specific() -> None:
    """Detect returns specific provider for matching URL."""
    registry = ProviderRegistry()
    mock = MockProvider()
    open_mock = MockOpenProvider()
    registry.register(mock)
    registry.register(open_mock)
    detected = registry.detect_provider("https://mock.com/video")
    assert detected.name == mock.name


def test_detect_provider_fallback() -> None:
    """Detect falls back to generic for unknown URL."""
    registry = ProviderRegistry()
    mock = MockProvider()
    open_mock = MockOpenProvider()
    registry.register(mock)
    registry.register(open_mock)
    detected = registry.detect_provider("https://unknown.com/video")
    assert detected.name == open_mock.name


def test_detect_provider_no_match() -> None:
    """Detect raises ProviderError when no match."""
    registry = ProviderRegistry()
    mock = MockProvider()
    registry.register(mock)
    with pytest.raises(ProviderError):
        registry.detect_provider("https://unknown.com/video")


def test_list_providers() -> None:
    """List returns all registered providers."""
    registry = ProviderRegistry()
    registry.register(MockProvider())
    providers = registry.list_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "MockProvider"
    assert providers[0]["kind"] == "media"


def test_is_registered_true() -> None:
    """is_registered returns True for registered providers."""
    registry = ProviderRegistry()
    mock = MockProvider()
    registry.register(mock)
    assert registry.is_registered(mock.provider_type) is True


def test_is_registered_false() -> None:
    """is_registered returns False for unregistered providers."""
    registry = ProviderRegistry()
    assert registry.is_registered(ProviderType.FACEBOOK) is False


def test_provider_count() -> None:
    """Count reflects registered providers."""
    registry = ProviderRegistry()
    assert registry.media_provider_count == 0
    registry.register(MockProvider())
    assert registry.media_provider_count == 1


def test_registration_order_preserved() -> None:
    """Detection respects registration order."""
    registry = ProviderRegistry()
    first = MockProvider()
    second = MockOpenProvider()
    registry.register(first)
    registry.register(second)
    detected = registry.detect_provider("https://mock.com/video")
    assert detected.name == first.name

