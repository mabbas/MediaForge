"""Desktop application configuration."""

from __future__ import annotations

import os
import platform
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class DesktopSettings(BaseSettings):
    """Desktop app settings.

    Loaded from env vars with GID_DESKTOP_ prefix.
    """

    # Window
    window_title: str = "GrabItDown"
    window_width: int = 1200
    window_height: int = 800
    window_min_width: int = 800
    window_min_height: int = 600
    start_minimized: bool = False
    always_on_top: bool = False

    # Server
    server_host: str = "127.0.0.1"
    server_port: int = 8765

    # Behavior
    minimize_to_tray: bool = True
    start_on_login: bool = False
    check_clipboard: bool = True
    show_notifications: bool = True

    # Paths
    data_dir: str = ""
    log_dir: str = ""

    model_config = {
        "env_prefix": "GID_DESKTOP_",
        "env_file": ".env",
        "extra": "ignore",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.data_dir:
            self.data_dir = str(self._default_data_dir())
        if not self.log_dir:
            self.log_dir = str(Path(self.data_dir) / "logs")

    @staticmethod
    def _default_data_dir() -> Path:
        """Platform-specific data directory."""
        system = platform.system()
        home = Path.home()

        if system == "Windows":
            base = Path(
                os.environ.get(
                    "APPDATA",
                    home / "AppData" / "Roaming",
                )
            )
            return base / "GrabItDown"
        elif system == "Darwin":
            return home / "Library" / "Application Support" / "GrabItDown"
        else:
            return home / ".config" / "grabitdown"


@lru_cache()
def get_desktop_settings() -> DesktopSettings:
    """Return cached desktop settings instance."""
    return DesktopSettings()

