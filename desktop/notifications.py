"""Native notification system — sends OS-level notifications for download events.

Uses:
- System tray notifications (if tray available)
- plyer as fallback (cross-platform)
- Desktop notification APIs
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class NotificationManager:
    """Cross-platform notification manager."""

    def __init__(self, tray: object | None = None) -> None:
        self._tray = tray
        self._enabled = True
        self._history: list[dict[str, str]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def notify(
        self,
        title: str,
        message: str,
        notification_type: str = "info",
    ) -> None:
        """Send a native notification.

        notification_type: info, success, warning, error
        """
        if not self._enabled:
            return

        self._history.append(
            {
                "title": title,
                "message": message,
                "type": notification_type,
            }
        )

        # Try tray notification first
        if self._tray:
            try:
                self._tray.show_notification(title, message)
                return
            except Exception:
                pass

        # Try plyer
        try:
            from plyer import notification

            notification.notify(
                title=title,
                message=message,
                app_name="GrabItDown",
                timeout=5,
            )
            return
        except ImportError:
            pass

        # Fallback: log only
        logger.info(
            "Notification: [%s] %s — %s",
            notification_type,
            title,
            message,
        )

    def download_started(self, title: str, url: str) -> None:
        self.notify(
            "Download Started",
            f"📥 {title or url[:50]}",
            "info",
        )

    def download_completed(
        self, title: str, file_path: str
    ) -> None:
        self.notify(
            "Download Complete ✓",
            f"✅ {title}",
            "success",
        )

    def download_failed(self, title: str, error: str) -> None:
        self.notify(
            "Download Failed",
            f"❌ {title}: {error[:80]}",
            "error",
        )

    def clipboard_url_detected(self, url: str) -> None:
        self.notify(
            "Media URL Detected",
            f"🔗 {url[:60]}... Click to download",
            "info",
        )

    def get_history(self) -> list[dict[str, str]]:
        return list(self._history[-50:])

    def clear_history(self) -> None:
        self._history.clear()
