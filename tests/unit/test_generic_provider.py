"""Tests for GrabItDown Generic provider."""

from src.models.enums import ProviderType
from src.providers.generic.provider import GenericProvider


def test_generic_name() -> None:
    """Provider name is 'Generic'."""
    p = GenericProvider()
    assert p.name == "Generic"


def test_generic_provider_type() -> None:
    """Provider type is GENERIC."""
    p = GenericProvider()
    assert p.provider_type == ProviderType.GENERIC


def test_generic_capabilities() -> None:
    """Capabilities reflect fallback nature."""
    p = GenericProvider()
    caps = p.capabilities
    assert caps.supports_playlists is False
    assert caps.supports_subtitles is False
    assert caps.supported_domains == []


def test_generic_can_handle_any_url() -> None:
    """Accepts any valid HTTP URL."""
    p = GenericProvider()
    assert p.can_handle("https://anything.com/video") is True
    assert p.can_handle("https://random-site.org/file.mp4") is True
    assert p.can_handle("http://example.com") is True


def test_generic_rejects_invalid_url() -> None:
    """Rejects non-HTTP URLs."""
    p = GenericProvider()
    assert p.can_handle("not-a-url") is False
    assert p.can_handle("ftp://files.com/video") is False

