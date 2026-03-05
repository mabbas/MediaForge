from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class JSBridge:
    def __init__(self, app=None):
        self._app = app

    def select_download_folder(self):
        if self._app and getattr(self._app, "dialogs", None):
            return self._app.dialogs.select_folder(title="Select Download Folder")
        return None

    def open_file(self, file_path: str):
        try:
            path = Path(file_path)
            if not path.exists():
                return False
            if platform.system() == "Windows":
                os.startfile(str(path))
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            return True
        except Exception as e:
            logger.error("Open file failed: %s", e)
            return False

    def open_folder(self, file_path: str):
        try:
            path = Path(file_path)
            folder = path.parent if path.is_file() else path
            if not folder.exists():
                return False
            sys_ = platform.system()
            if sys_ == "Windows":
                if path.is_file():
                    subprocess.Popen(["explorer", "/select,", str(path)])
                else:
                    os.startfile(str(folder))
            elif sys_ == "Darwin":
                if path.is_file():
                    subprocess.Popen(["open", "-R", str(path)])
                else:
                    subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            return True
        except Exception as e:
            logger.error("Open folder failed: %s", e)
            return False

    def select_batch_file(self):
        if self._app and getattr(self._app, "dialogs", None):
            return self._app.dialogs.select_file(title="Select URL File", file_types=[("Text Files", "*.txt"), ("All Files", "*.*")])
        return None

    def select_video_file(self):
        """Open file picker for clip source (local video). Returns path or None."""
        if self._app and getattr(self._app, "dialogs", None):
            return self._app.dialogs.select_file(
                title="Select Video to Clip",
                file_types=[
                    ("Video", "*.mp4;*.mkv;*.webm;*.avi;*.mov;*.m4v;*.flv"),
                    ("All Files", "*.*"),
                ],
            )
        return None

    def get_system_info(self):
        try:
            import psutil
            m = psutil.virtual_memory()
            return {"platform": platform.system(), "platform_version": platform.version(), "python_version": sys.version.split()[0], "cpu_count": psutil.cpu_count() or 0, "ram_total_gb": round(m.total / (1024**3), 1), "ram_available_gb": round(m.available / (1024**3), 1)}
        except ImportError:
            return {"platform": platform.system(), "platform_version": platform.version(), "python_version": sys.version.split()[0], "cpu_count": 0, "ram_total_gb": 0.0, "ram_available_gb": 0.0}

    def get_app_version(self):
        from desktop.version import APP_ID, APP_NAME, APP_VERSION
        return {"name": APP_NAME, "version": APP_VERSION, "id": APP_ID}

    def toggle_clipboard_monitor(self, enabled: bool):
        if self._app and getattr(self._app, "clipboard", None):
            self._app.clipboard.enabled = enabled
            return True
        return False

    def toggle_notifications(self, enabled: bool):
        if self._app and getattr(self._app, "notifications", None):
            self._app.notifications.enabled = enabled
            return True
        return False

    def get_settings(self):
        if not self._app:
            return {}
        s = getattr(self._app, "settings", None)
        c = getattr(self._app, "clipboard", None)
        n = getattr(self._app, "notifications", None)
        return {"clipboard_enabled": c.enabled if c else False, "notifications_enabled": n.enabled if n else True, "minimize_to_tray": s.minimize_to_tray if s else True, "start_on_login": s.start_on_login if s else False}

    def save_preferences(self, prefs: dict):
        if self._app and getattr(self._app, "settings", None):
            from desktop.persistence import save_desktop_prefs
            return save_desktop_prefs(prefs, self._app.settings.data_dir)
        return False
