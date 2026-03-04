"""Tests for GrabItDown custom exceptions."""

from src.exceptions import (
    ConfigurationError,
    DownloadError,
    FeatureDisabledError,
    GrabItDownError,
    IntegrityError,
    LimitExceededError,
    NetworkError,
    ProviderError,
    ResumeError,
    TranscriptError,
)


def test_grabitdown_error_message() -> None:
    """Base exception stores message."""
    err = GrabItDownError("test error")
    assert str(err) == "test error"
    assert err.message == "test error"


def test_provider_error_with_provider() -> None:
    """ProviderError includes provider name."""
    err = ProviderError("failed", provider="YouTube")
    assert "[YouTube]" in str(err)
    assert err.provider == "YouTube"


def test_provider_error_without_provider() -> None:
    """ProviderError works without provider name."""
    err = ProviderError("failed")
    assert str(err) == "failed"


def test_download_error_with_url() -> None:
    """DownloadError includes URL."""
    err = DownloadError("failed", url="https://example.com")
    assert err.url == "https://example.com"


def test_feature_disabled_error() -> None:
    """FeatureDisabledError shows feature and tier."""
    err = FeatureDisabledError(feature_name="playlist_download", required_tier="Pro")
    assert "playlist_download" in str(err)
    assert "GrabItDown Pro" in str(err)


def test_limit_exceeded_error() -> None:
    """LimitExceededError shows usage vs limit."""
    err = LimitExceededError(feature_name="daily_downloads", current_usage=5, max_allowed=5)
    assert "5/5" in str(err)


def test_exception_hierarchy() -> None:
    """All exceptions inherit from GrabItDownError."""
    assert issubclass(ProviderError, GrabItDownError)
    assert issubclass(DownloadError, GrabItDownError)
    assert issubclass(ResumeError, DownloadError)
    assert issubclass(TranscriptError, GrabItDownError)
    assert issubclass(FeatureDisabledError, GrabItDownError)
    assert issubclass(ConfigurationError, GrabItDownError)
    assert issubclass(NetworkError, GrabItDownError)
    assert issubclass(IntegrityError, GrabItDownError)

