"""GrabItDown provider registry — discovers and manages providers."""

from __future__ import annotations

import logging
import threading
from typing import Dict, List

from src.core.base_provider import BaseFileProvider, BaseMediaProvider
from src.exceptions import ProviderError
from src.models.enums import ProviderType

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Thread-safe registry for media and file providers.

    Providers are registered in priority order. When detecting a provider
    for a URL, the first matching provider wins. Register Generic provider
    LAST so it is the fallback.
    """

    def __init__(self) -> None:
        self._media_providers: Dict[ProviderType, BaseMediaProvider] = {}
        self._file_providers: Dict[ProviderType, BaseFileProvider] = {}
        self._provider_order: List[ProviderType] = []
        self._lock = threading.Lock()

    def register(self, provider: BaseMediaProvider) -> None:
        """Register a media provider.

        Providers are stored by type and also in registration order for
        priority-based detection.
        """
        with self._lock:
            ptype = provider.provider_type
            self._media_providers[ptype] = provider
            if ptype not in self._provider_order:
                self._provider_order.append(ptype)
            logger.info("Registered media provider: %s (%s)", provider.name, ptype.value)

    def register_file_provider(self, provider: BaseFileProvider) -> None:
        """Register a file provider (Mega, Dropbox, etc.)."""
        with self._lock:
            ptype = provider.provider_type
            self._file_providers[ptype] = provider
            logger.info("Registered file provider: %s (%s)", provider.name, ptype.value)

    def get_provider(self, provider_type: ProviderType) -> BaseMediaProvider:
        """Get a specific media provider by type.

        Raises ProviderError if not registered.
        """
        provider = self._media_providers.get(provider_type)
        if not provider:
            raise ProviderError(
                f"Provider '{provider_type.value}' is not registered",
                provider=provider_type.value,
            )
        return provider

    def get_file_provider(self, provider_type: ProviderType) -> BaseFileProvider:
        """Get a specific file provider by type.

        Raises ProviderError if not registered.
        """
        provider = self._file_providers.get(provider_type)
        if not provider:
            raise ProviderError(
                f"File provider '{provider_type.value}' is not registered",
                provider=provider_type.value,
            )
        return provider

    def detect_provider(self, url: str) -> BaseMediaProvider:
        """Auto-detect the best provider for a URL.

        Iterates registered providers in registration order. Returns the
        first provider whose can_handle() returns True.

        Raises ProviderError if no provider can handle the URL.
        """
        for ptype in self._provider_order:
            provider = self._media_providers[ptype]
            if provider.can_handle(url):
                logger.debug("URL '%s...' matched provider: %s", url[:80], provider.name)
                return provider
        raise ProviderError(f"No provider can handle URL: {url[:100]}")

    def list_providers(self) -> list[dict]:
        """List all registered providers with their capabilities.

        Returns list of dicts with keys: name, type, capabilities, kind ('media' or 'file').
        """
        result: list[dict] = []
        for ptype in self._provider_order:
            provider = self._media_providers[ptype]
            result.append(
                {
                    "name": provider.name,
                    "type": ptype.value,
                    "capabilities": provider.capabilities.model_dump(),
                    "kind": "media",
                }
            )
        for ptype, provider in self._file_providers.items():
            result.append(
                {
                    "name": provider.name,
                    "type": ptype.value,
                    "capabilities": provider.capabilities.model_dump(),
                    "kind": "file",
                }
            )
        return result

    def is_registered(self, provider_type: ProviderType) -> bool:
        """Check if a provider type is registered."""
        return provider_type in self._media_providers or provider_type in self._file_providers

    @property
    def media_provider_count(self) -> int:
        """Number of registered media providers."""
        return len(self._media_providers)

    @property
    def file_provider_count(self) -> int:
        """Number of registered file providers."""
        return len(self._file_providers)

