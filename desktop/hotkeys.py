"""Global hotkey support — registers system-wide keyboard shortcuts.

Default hotkeys:
- Ctrl+Shift+D: Quick download from clipboard
- Ctrl+Shift+V: Paste URL and download
- Ctrl+Shift+P: Pause/Resume downloads
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# Try to import keyboard library
try:
    import keyboard

    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False
    logger.info(
        "keyboard library not installed. "
        "Global hotkeys disabled."
    )


class HotkeyManager:
    """Manages global keyboard shortcuts.

    Hotkeys are system-wide (work even when app is minimized/unfocused).
    """

    def __init__(self) -> None:
        self._hotkeys: dict[str, Callable[[], None]] = {}
        self._registered: list[str] = []
        self._enabled = True

    @property
    def is_available(self) -> bool:
        return HAS_KEYBOARD

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        if not value:
            self.unregister_all()
        logger.info(
            "Hotkeys: %s",
            "enabled" if value else "disabled",
        )

    def register(
        self,
        hotkey: str,
        callback: Callable[[], None],
        description: str = "",
    ) -> bool:
        """Register a global hotkey.

        hotkey: Key combination (e.g., 'ctrl+shift+d')
        callback: Function to call when pressed
        """
        if not HAS_KEYBOARD:
            return False

        try:

            def _wrapped() -> None:
                if self._enabled:
                    callback()

            keyboard.add_hotkey(hotkey, _wrapped)
            self._hotkeys[hotkey] = callback
            self._registered.append(hotkey)
            logger.info(
                "Hotkey registered: %s (%s)",
                hotkey,
                description,
            )
            return True
        except Exception as e:
            logger.warning("Failed to register %s: %s", hotkey, e)
            return False

    def unregister(self, hotkey: str) -> None:
        """Unregister a hotkey."""
        if HAS_KEYBOARD and hotkey in self._registered:
            try:
                keyboard.remove_hotkey(hotkey)
                self._registered.remove(hotkey)
            except Exception:
                pass

    def unregister_all(self) -> None:
        """Unregister all hotkeys."""
        if not HAS_KEYBOARD:
            return
        for hk in list(self._registered):
            try:
                keyboard.remove_hotkey(hk)
            except Exception:
                pass
        self._registered.clear()

    def list_hotkeys(self) -> list[dict[str, object]]:
        """List registered hotkeys."""
        return [
            {"hotkey": hk, "registered": True}
            for hk in self._registered
        ]
