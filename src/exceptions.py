"""Custom exception hierarchy for GrabItDown."""

from __future__ import annotations


class GrabItDownError(Exception):
    """Base exception for all GrabItDown errors."""

    def __init__(self, message: str) -> None:
        """Initialize the base GrabItDownError."""
        self.message = message
        super().__init__(self.message)


class ProviderError(GrabItDownError):
    """Raised when a provider encounters an error."""

    def __init__(self, message: str, provider: str | None = None) -> None:
        """Initialize the ProviderError with optional provider name."""
        self.provider = provider
        formatted = f"[{provider}] {message}" if provider else message
        super().__init__(formatted)


class DownloadError(GrabItDownError):
    """Raised when a download fails."""

    def __init__(self, message: str, url: str | None = None) -> None:
        """Initialize the DownloadError with optional URL."""
        self.url = url
        super().__init__(message)


class ResumeError(DownloadError):
    """Raised when download resume fails."""


class DownloadCancelledError(DownloadError):
    """Raised when a download is cancelled by the user."""


class TranscriptError(GrabItDownError):
    """Raised when transcript extraction fails."""


class FeatureDisabledError(GrabItDownError):
    """Raised when accessing a disabled feature."""

    def __init__(self, feature_name: str, required_tier: str = "Pro") -> None:
        """Initialize the FeatureDisabledError with feature and required tier."""
        self.feature_name = feature_name
        self.required_tier = required_tier
        message = f"'{feature_name}' requires GrabItDown {required_tier} or higher"
        super().__init__(message)


class LimitExceededError(GrabItDownError):
    """Raised when a usage limit is exceeded."""

    def __init__(self, feature_name: str, current_usage: int, max_allowed: int) -> None:
        """Initialize the LimitExceededError with usage details."""
        self.feature_name = feature_name
        self.current_usage = current_usage
        self.max_allowed = max_allowed
        message = f"'{feature_name}' limit exceeded: {current_usage}/{max_allowed}"
        super().__init__(message)


class ConfigurationError(GrabItDownError):
    """Raised for configuration-related errors."""


class NetworkError(GrabItDownError):
    """Raised for network connectivity issues."""


class IntegrityError(GrabItDownError):
    """Raised when file integrity check fails."""

