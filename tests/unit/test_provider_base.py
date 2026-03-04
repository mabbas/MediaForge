"""Tests for GrabItDown provider base classes."""

import pytest

from src.core.base_provider import BaseMediaProvider
from src.core.interfaces import ProviderCapabilities
from src.models.download import DownloadRequest, DownloadResult
from src.models.enums import ProviderType
from src.models.media import MediaFormat, MediaInfo


class MockProvider(BaseMediaProvider):
    """Mock provider for testing base class methods."""

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


class MockOpenProvider(MockProvider):
    """Mock provider that accepts any valid HTTP URL."""

    @property
    def name(self) -> str:
        """Return open mock provider name."""
        return "MockOpenProvider"

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return capabilities with no domain restriction."""
        return ProviderCapabilities(supported_domains=[])


def test_validate_url_valid_https() -> None:
    """HTTPS URL with matching domain passes."""
    p = MockProvider()
    assert p.validate_url("https://mock.com/video") is True


def test_validate_url_valid_http() -> None:
    """HTTP URL passes."""
    assert MockProvider().validate_url("http://mock.com/video") is True


def test_validate_url_with_www() -> None:
    """www prefix is handled correctly."""
    assert MockProvider().validate_url("https://www.mock.com/video") is True


def test_validate_url_subdomain() -> None:
    """Subdomain of supported domain passes."""
    assert MockProvider().validate_url("https://m.mock.com/video") is True


def test_validate_url_invalid_scheme() -> None:
    """FTP URL fails."""
    assert MockProvider().validate_url("ftp://mock.com/file") is False


def test_validate_url_no_scheme() -> None:
    """URL without scheme fails."""
    assert MockProvider().validate_url("mock.com/video") is False


def test_validate_url_wrong_domain() -> None:
    """URL with non-matching domain fails."""
    assert MockProvider().validate_url("https://other.com/video") is False


def test_validate_url_empty_domains() -> None:
    """Provider with no domain restriction accepts any valid URL."""
    p = MockOpenProvider()
    assert p.validate_url("https://anything.com/video") is True


def test_sanitize_filename_basic() -> None:
    """Basic title passes through."""
    p = MockProvider()
    assert p.sanitize_filename("My Video Title") == "My Video Title"


def test_sanitize_filename_special_chars() -> None:
    """Special characters replaced with underscore."""
    p = MockProvider()
    result = p.sanitize_filename('Video: Test/File <2024> "quotes"')
    assert "/" not in result
    assert ":" not in result
    assert "<" not in result
    assert '"' not in result


def test_sanitize_filename_length() -> None:
    """Long titles truncated to 200 chars."""
    p = MockProvider()
    long_title = "A" * 300
    result = p.sanitize_filename(long_title)
    assert len(result) <= 200


def test_sanitize_filename_empty() -> None:
    """Empty string returns 'untitled'."""
    p = MockProvider()
    assert p.sanitize_filename("") == "untitled"


def test_sanitize_filename_only_special() -> None:
    """Title with only special chars → 'untitled'."""
    p = MockProvider()
    assert p.sanitize_filename("::///***") == "untitled"


def test_sanitize_filename_dots_stripped() -> None:
    """Leading/trailing dots removed."""
    p = MockProvider()
    result = p.sanitize_filename("...video...")
    assert not result.startswith(".")
    assert not result.endswith(".")

