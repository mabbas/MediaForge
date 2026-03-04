"""System tray integration — shows GrabItDown icon in system tray with context menu.

Uses pystray for cross-platform tray support.
"""

from __future__ import annotations

import logging
import platform
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import pystray (optional dependency)
try:
    import pystray
    from PIL import Image

    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
    logger.info(
        "pystray/PIL not installed. "
        "System tray disabled."
    )


class SystemTray:
    """System tray icon with context menu.

    Menu items:
    - Show Window
    - Pause/Resume Downloads
    - ──────────
    - Status: 2 active, 3 queued
    - ──────────
    - Settings
    - Quit
    """

    def __init__(
        self,
        on_show: callable = None,
        on_quit: callable = None,
        on_pause: callable = None,
        on_resume: callable = None,
    ):
        self._on_show = on_show
        self._on_quit = on_quit
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._icon = None
        self._thread = None
        self._status_text = "Idle"
        self._is_paused = False

    @property
    def is_available(self) -> bool:
        return HAS_TRAY

    def start(self) -> None:
        """Start tray icon in background thread."""
        if not HAS_TRAY:
            logger.info("Tray not available")
            return

        self._thread = threading.Thread(
            target=self._run_tray,
            name="gid-tray",
            daemon=True,
        )
        self._thread.start()
        logger.info("System tray started")

    def stop(self) -> None:
        """Remove tray icon."""
        if self._icon:
            self._icon.stop()
        logger.info("System tray stopped")

    def update_status(
        self,
        active: int = 0,
        queued: int = 0,
        is_paused: bool = False,
    ) -> None:
        """Update tray tooltip and status."""
        self._is_paused = is_paused
        if active > 0:
            self._status_text = (
                f"{active} downloading, {queued} queued"
            )
        elif queued > 0:
            self._status_text = f"{queued} queued"
        else:
            self._status_text = "Idle"

        if self._icon:
            self._icon.title = (
                f"GrabItDown — {self._status_text}"
            )

    def show_notification(
        self,
        title: str,
        message: str,
    ) -> None:
        """Show a native notification via tray."""
        if self._icon and HAS_TRAY:
            try:
                self._icon.notify(message, title)
            except Exception as e:
                logger.debug(
                    "Notification failed: %s", e
                )

    def _run_tray(self) -> None:
        """Create and run tray icon."""
        image = self._create_icon()
        menu = self._create_menu()

        self._icon = pystray.Icon(
            name="GrabItDown",
            icon=image,
            title="GrabItDown",
            menu=menu,
        )

        self._icon.run()

    def _create_icon(self) -> "Image.Image":
        """Create tray icon image.

        Uses a simple colored square. In production,
        replace with actual .ico/.png icon file.
        """
        # Create a simple 64x64 icon
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))

        # Draw a gradient-ish icon
        for x in range(64):
            for y in range(64):
                # Circle mask
                dx = x - 32
                dy = y - 32
                if dx * dx + dy * dy <= 30 * 30:
                    r = int(0 + (x / 64) * 100)
                    g = int(180 + (y / 64) * 75)
                    b = int(255 - (x / 64) * 50)
                    img.putpixel((x, y), (r, g, b, 255))

        return img

    def _create_menu(self) -> "pystray.Menu":
        """Create tray context menu."""
        return pystray.Menu(
            pystray.MenuItem(
                "Show GrabItDown",
                self._action_show,
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda icon, item: (
                    "Resume Downloads"
                    if self._is_paused
                    else "Pause Downloads"
                ),
                self._action_toggle_pause,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda icon, item: self._status_text,
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Quit GrabItDown",
                self._action_quit,
            ),
        )

    def _action_show(self, icon: object, item: object) -> None:
        if self._on_show:
            self._on_show()

    def _action_toggle_pause(self, icon: object, item: object) -> None:
        if self._is_paused and self._on_resume:
            self._on_resume()
        elif not self._is_paused and self._on_pause:
            self._on_pause()

    def _action_quit(self, icon: object, item: object) -> None:
        if self._icon:
            self._icon.stop()
        if self._on_quit:
            self._on_quit()
