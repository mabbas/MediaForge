"""GrabItDown provider factory — creates and registers providers based on configuration."""

from __future__ import annotations

import logging

from src.config import get_settings
from src.core.provider_registry import ProviderRegistry
from src.providers.generic.provider import GenericProvider
from src.providers.youtube.provider import YouTubeProvider

logger = logging.getLogger(__name__)


def create_provider_registry() -> ProviderRegistry:
    """Create and configure the provider registry."""
    settings = get_settings()
    registry = ProviderRegistry()

    # YouTube
    if settings.providers.youtube.enabled:
        yt_provider = YouTubeProvider(
            cookies_file=settings.providers.youtube.cookies_file,
            geo_bypass=settings.providers.youtube.geo_bypass,
        )
        registry.register(yt_provider)
        logger.info("YouTube provider enabled")
    else:
        logger.info("YouTube provider disabled")

    # Facebook provider would be added here in the future.

    # Generic — always last (fallback)
    if settings.providers.generic.enabled:
        registry.register(GenericProvider())
        logger.info("Generic provider enabled (fallback)")
    else:
        logger.info("Generic provider disabled")

    logger.info(
        "Provider registry initialized: %s media, %s file providers",
        registry.media_provider_count,
        registry.file_provider_count,
    )

    return registry

