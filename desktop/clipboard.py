"""Clipboard URL monitor for media URLs and optional auto-queue."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

URL_PATTERNS = [
    r"https?://(www\.)?youtube\.com/watch\?v=",
    r"https?://(www\.)?youtu\.be/",
    r"https?://(www\.)?youtube\.com/playlist\?list=",
    r"https?://(www\.)?youtube\.com/shorts/",
    r"https?://(www\.)?vimeo\.com/\d+",
    r"https?://(www\.)?dailymotion\.com/video/",
    r"https?://(www\.)?twitch\.tv/videos/",
    r"https?://(www\.)?soundcloud\.com/",
    r"https?://(www\.)?twitter\.com/.+/status/",
    r"https?://(www\.)?x\.com/.+/status/",
    r"https?://(www\.)?instagram\.com/(p|reel)/",
    r"https?://(www\.)?tiktok\.com/.+/video/",
    r"https?://(www\.)?facebook\.com/.+/videos/",
    r"https?://(www\.)?reddit\.com/r/.+/comments/",
]

COMPILED_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in URL_PATTERNS
]


def is_media_url(url: str) -> bool:
    """Check if a URL matches known media patterns."""
    if not url or not url.startswith("http"):
        return False
    for pattern in COMPILED_PATTERNS:
        if pattern.search(url):
            return True
    return False


class ClipboardMonitor:
    """Monitors clipboard for media URLs. on_url_detected(url) called when found."""

    def __init__(
        self,
        on_url_detected: Callable[[str], None] | None = None,
        check_interval: float = 1.5,
    ):
        self._on_url_detected = on_url_detected
        self._interval = check_interval
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_url = ""
        self._seen_urls: set[str] = set()
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        logger.info(
            "Clipboard monitor: %s",
            "enabled" if value else "disabled",
        )

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="gid-clipboard-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("Clipboard monitor started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Clipboard monitor stopped")

    def clear_history(self) -> None:
        """Clear seen URLs so they can be re-detected."""
        self._seen_urls.clear()
        self._last_url = ""

    def _monitor_loop(self) -> None:
        while self._running:
            if self._enabled:
                self._check_clipboard()
            time.sleep(self._interval)

    def _check_clipboard(self) -> None:
        try:
            text = self._get_clipboard_text()
            if not text:
                return
            text = text.strip()
            if text == self._last_url:
                return
            self._last_url = text
            if text in self._seen_urls:
                return
            if is_media_url(text):
                self._seen_urls.add(text)
                logger.info("Media URL detected: %s...", text[:80])
                if self._on_url_detected:
                    self._on_url_detected(text)
        except Exception as e:
            logger.debug("Clipboard check error: %s", e)

    def _get_clipboard_text(self) -> str | None:
        """Get text from clipboard (pyperclip or tkinter fallback)."""
        try:
            import pyperclip
            return pyperclip.paste()
        except ImportError:
            pass
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text
        except Exception:
            return None
