"""GrabItDown abstract base classes for providers."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from urllib.parse import urlparse

from src.core.interfaces import ProgressCallback, ProviderCapabilities
from src.exceptions import DownloadError, ProviderError
from src.models.download import DownloadRequest, DownloadResult
from src.models.media import MediaFormat, MediaInfo
from src.models.enums import ProviderType

logger = logging.getLogger(__name__)


class BaseMediaProvider(ABC):
    """Abstract base for media providers (YouTube, Facebook, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Provider type enum value."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Provider capability flags."""
        ...

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Check if this provider can handle the URL."""
        ...

    @abstractmethod
    def extract_info(self, url: str) -> MediaInfo:
        """Extract media metadata without downloading.

        Raises ProviderError on failure.
        """
        ...

    @abstractmethod
    def get_formats(self, url: str) -> list[MediaFormat]:
        """List available download formats.

        Raises ProviderError on failure.
        """
        ...

    @abstractmethod
    def download(
        self,
        request: DownloadRequest,
        output_dir: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download media file.

        Raises DownloadError on failure.
        """
        ...

    def validate_url(self, url: str) -> bool:
        """Validate URL format and optionally check against supported domains.

        Returns True if:
        - URL starts with http:// or https://
        - If supported_domains is non-empty, URL domain matches one of them
        - If supported_domains is empty, any valid HTTP URL passes
        """
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            if not parsed.netloc:
                return False

            domains = self.capabilities.supported_domains
            if not domains:
                return True

            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]

            clean_domains = [d[4:] if d.startswith("www.") else d for d in domains]

            if domain in clean_domains:
                return True
            return any(domain.endswith(f".{d}") for d in clean_domains)
        except Exception:
            return False

    def sanitize_filename(self, title: str) -> str:
        """Remove invalid filesystem characters and limit filename length.

        - Replace: / \\ : * ? \" < > | with underscore
        - Strip leading/trailing whitespace and dots
        - Collapse multiple underscores/spaces
        - Limit to 200 characters
        - Return 'untitled' if result is empty
        """
        if not title:
            return "untitled"

        cleaned = re.sub(r'[/\\:*?"<>|]', "_", title)
        cleaned = re.sub(r"[\x00-\x1f\x7f]", "", cleaned)
        cleaned = re.sub(r"[_\s]+", " ", cleaned)
        cleaned = cleaned.strip(". ")

        if len(cleaned) > 200:
            truncated = cleaned[:200]
            if " " in truncated:
                truncated = truncated.rsplit(" ", 1)[0]
            cleaned = truncated

        return cleaned if cleaned else "untitled"


class BaseFileProvider(ABC):
    """Abstract base for file providers (Mega, Dropbox, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Provider type enum value."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Provider capability flags."""
        ...

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Check if this provider can handle the URL."""
        ...

    @abstractmethod
    def get_file_info(self, url: str) -> dict:
        """Get file metadata (name, size, type)."""
        ...

    @abstractmethod
    def download_file(
        self,
        url: str,
        output_path: str,
        progress_callback: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download file directly.

        Raises DownloadError on failure.
        """
        ...

