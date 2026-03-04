"""High-level GrabItDown facade — primary public API for the downloader engine."""

from __future__ import annotations

import logging
from typing import List, Optional

from src.config import get_settings
from src.core.provider_factory import create_provider_registry
from src.core.provider_registry import ProviderRegistry
from src.download.bandwidth_throttle import GlobalBandwidthThrottle
from src.download.disk_monitor import DiskMonitor
from src.download.download_engine import DownloadEngine
from src.download.state_persistence import StatePersistence
from src.exceptions import DownloadError, FeatureDisabledError
from src.features.feature_flags import load_feature_flags
from src.features.feature_gate import FeatureGate
from src.features.usage_tracker import UsageTracker
from src.models.download import DownloadJob, DownloadRequest
from src.models.enums import MediaType, Quality
from src.models.playlist import PlaylistDownloadRequest
from src.resume.integrity_checker import IntegrityChecker
from src.resume.network_monitor import NetworkMonitor
from src.resume.recovery_manager import RecoveryManager
from src.resume.retry_handler import RetryConfig, RetryHandler

logger = logging.getLogger(__name__)


class GrabItDown:
    """High-level facade that wires together all GrabItDown subsystems."""

    def __init__(
        self,
        registry: Optional[ProviderRegistry] = None,
        engine: Optional[DownloadEngine] = None,
        feature_gate: Optional[FeatureGate] = None,
        usage_tracker: Optional[UsageTracker] = None,
        recovery_manager: Optional[RecoveryManager] = None,
        network_monitor: Optional[NetworkMonitor] = None,
        retry_handler: Optional[RetryHandler] = None,
    ) -> None:
        self._settings = get_settings()

        # Providers and engine
        self._registry: ProviderRegistry = registry or create_provider_registry()
        self._engine: DownloadEngine = engine or DownloadEngine(self._registry)

        # Features and usage limits
        flags = load_feature_flags()
        self._gate: FeatureGate = feature_gate or FeatureGate(flags)
        self._usage: UsageTracker = usage_tracker or UsageTracker(self._gate)

        # Resume & recovery
        self._recovery_manager: RecoveryManager = recovery_manager or RecoveryManager()
        self._integrity_checker = IntegrityChecker()

        # Network monitoring and retries
        self._network_monitor: NetworkMonitor = network_monitor or NetworkMonitor()
        self._network_monitor.on_disconnected(self._on_network_lost)
        self._network_monitor.on_connected(self._on_network_restored)

        self._retry_handler: RetryHandler = retry_handler or RetryHandler(RetryConfig())

        output_dir = self._settings.download.output_directory
        self._disk_monitor = DiskMonitor(
            download_dir=output_dir,
            min_space_mb=self._settings.download.min_disk_space_mb,
        )
        self._bandwidth_throttle = GlobalBandwidthThrottle.get_instance(
            self._settings.download.total_bandwidth_limit,
        )
        self._state_persistence = StatePersistence(state_dir=output_dir)

        logger.info("GrabItDown facade initialized")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background services such as network monitoring."""
        if self._state_persistence.has_saved_state():
            saved = self._state_persistence.load_state()
            if saved:
                logger.info("Found %s saved jobs", len(saved))
            self._state_persistence.clear_state()
        self._network_monitor.start()

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the engine and background services."""
        try:
            self._network_monitor.stop()
            queued = self._engine._queue.get_all_jobs()
            active = self._engine.get_active_jobs()
            if queued or active:
                self._state_persistence.save_state(queued, active)
        finally:
            self._engine.shutdown(wait=wait)

    # ── Download APIs ────────────────────────────────────────────────────────

    def download(
        self,
        url: str,
        mode: str = "video",
        quality: str = "1080p",
        output_dir: Optional[str] = None,
        user_id: str = "system",
        tier: Optional[str] = None,
        priority: str = "normal",
    ) -> DownloadJob:
        """Submit a single download via the facade."""
        # Feature check
        feature_name = "video_download" if mode == "video" else "audio_download"
        access = self._gate.check_access(feature_name, tier=tier)
        if not access.allowed:
            raise FeatureDisabledError(feature_name, access.reason or "Pro")

        # Disk space check
        can_proceed, reason = self._disk_monitor.check_before_download()
        if not can_proceed:
            raise DownloadError(reason)

        # Usage tracking (daily downloads)
        self._usage.increment(user_id, "daily_downloads", tier=tier)

        media_type = MediaType.VIDEO if mode == "video" else MediaType.AUDIO
        quality_enum = self._resolve_quality(quality)

        request = DownloadRequest(
            url=url,
            media_type=media_type,
            quality=quality_enum,
            output_directory=output_dir or self._settings.download.output_directory,
        )

        return self._engine.submit_download(
            request=request,
            priority=priority,
            user_id=user_id,
        )

    def download_playlist(
        self,
        url: str,
        mode: str = "video",
        quality: str = "1080p",
        output_dir: Optional[str] = None,
        items: str | List[int] = "all",
        concurrency: int = 3,
        user_id: str = "system",
        tier: Optional[str] = None,
        priority: str = "normal",
    ) -> List[DownloadJob]:
        """Submit a playlist for download."""
        # Feature check
        access = self._gate.check_access("playlist_download", tier=tier)
        if not access.allowed:
            raise FeatureDisabledError("playlist_download", access.reason or "Pro")

        media_type = MediaType.VIDEO if mode == "video" else MediaType.AUDIO
        quality_enum = self._resolve_quality(quality)

        playlist_request = PlaylistDownloadRequest(
            url=url,
            media_type=media_type,
            quality=quality_enum,
            items=items,
            concurrency=concurrency,
            output_directory=output_dir or self._settings.download.output_directory,
        )

        _, jobs = self._engine.submit_playlist(
            request=playlist_request,
            priority=priority,
            user_id=user_id,
        )
        return jobs

    def download_batch(
        self,
        urls: List[str],
        mode: str = "video",
        quality: str = "1080p",
        output_dir: Optional[str] = None,
        user_id: str = "system",
        tier: Optional[str] = None,
        priority: str = "normal",
    ) -> List[DownloadJob]:
        """Submit a batch of downloads."""
        # Feature check
        access = self._gate.check_access("batch_download", tier=tier)
        if not access.allowed:
            raise FeatureDisabledError("batch_download", access.reason or "Pro")

        media_type = MediaType.VIDEO if mode == "video" else MediaType.AUDIO
        quality_enum = self._resolve_quality(quality)

        requests = [
            DownloadRequest(
                url=url,
                media_type=media_type,
                quality=quality_enum,
                output_directory=output_dir or self._settings.download.output_directory,
            )
            for url in urls
        ]

        return self._engine.submit_batch(
            requests=requests,
            priority=priority,
            user_id=user_id,
        )

    # ── Status & Control ────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> Optional[DownloadJob]:
        """Get job status."""
        return self._engine.get_job(job_id)

    def get_all_jobs(self) -> List[DownloadJob]:
        """Get all jobs."""
        return self._engine.get_all_jobs()

    def get_stats(self) -> dict:
        """Get engine statistics."""
        return self._engine.get_stats()

    def cancel(self, job_id: str) -> bool:
        """Cancel a download."""
        return self._engine.cancel_job(job_id)

    def cancel_all(self) -> int:
        """Cancel all downloads."""
        return self._engine.cancel_all()

    def pause(self) -> None:
        """Pause download engine."""
        self._engine.pause_all()

    def resume(self) -> None:
        """Resume download engine."""
        self._engine.resume_all()

    # ── Info APIs ───────────────────────────────────────────────────────────

    def get_info(self, url: str) -> dict:
        """Get media info without downloading."""
        provider = self._registry.detect_provider(url)
        info = provider.extract_info(url)
        return info.model_dump()

    def get_formats(self, url: str) -> List[dict]:
        """Get available formats."""
        provider = self._registry.detect_provider(url)
        formats = provider.get_formats(url)
        return [f.model_dump() for f in formats]

    def list_providers(self) -> List[dict]:
        """List registered providers."""
        return self._registry.list_providers()

    # ── Recovery ────────────────────────────────────────────────────────────

    def get_recoverable_downloads(self) -> list:
        """Get list of recoverable downloads."""
        return self._recovery_manager.scan_incomplete()

    def cleanup_stale(self) -> int:
        """Clean up stale partial downloads."""
        return self._recovery_manager.cleanup_stale_downloads()

    # ── Feature & Usage Info ────────────────────────────────────────────────

    def get_features(self, tier: Optional[str] = None) -> dict:
        """Get feature availability for a tier."""
        return self._gate.list_all_features(tier=tier)

    def get_usage(self, user_id: str = "system") -> dict:
        """Get usage stats for a user."""
        return self._usage.get_all_usage(user_id)

    # ── Disk & bandwidth ──────────────────────────────────────────────────────

    def get_disk_stats(self) -> dict:
        """Get disk usage statistics for the download directory."""
        return self._disk_monitor.get_stats()

    def set_bandwidth_limit(self, bytes_per_second: int) -> None:
        """Set global bandwidth limit in bytes per second. 0 = unlimited."""
        self._bandwidth_throttle.set_limit(bytes_per_second)

    def get_bandwidth_limit(self) -> int:
        """Get current global bandwidth limit in bytes per second."""
        return self._bandwidth_throttle.limit

    # ── Network ─────────────────────────────────────────────────────────────

    @property
    def is_online(self) -> bool:
        """Check if network is available."""
        return self._network_monitor.is_connected

    # ── Private helpers ─────────────────────────────────────────────────────

    def _resolve_quality(self, quality: str) -> Quality:
        """Map quality string to Quality enum."""
        quality_map = {
            "2160p": Quality.Q_2160P,
            "4k": Quality.Q_2160P,
            "1440p": Quality.Q_1440P,
            "1080p": Quality.Q_1080P,
            "720p": Quality.Q_720P,
            "480p": Quality.Q_480P,
            "360p": Quality.Q_360P,
            "best": Quality.BEST,
            "worst": Quality.WORST,
        }
        return quality_map.get(quality.lower(), Quality.Q_1080P)

    def _on_network_lost(self) -> None:
        """Handle network disconnection."""
        logger.warning("Network lost — pausing downloads")
        self._engine.pause_all()

    def _on_network_restored(self) -> None:
        """Handle network reconnection."""
        logger.info("Network restored — resuming downloads")
        self._engine.resume_all()

