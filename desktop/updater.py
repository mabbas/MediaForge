from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)
try:
    from packaging import version as pkg_version
except ImportError:
    pkg_version = None


class UpdateChecker:
    def __init__(self, current_version: str, check_url: str = "https://api.github.com/repos/grabitdown/grabitdown/releases/latest", on_update_available=None):
        self._current = current_version
        self._check_url = check_url
        self._on_update = on_update_available
        self._latest = None
        self._download_url = None

    def check_async(self):
        threading.Thread(target=self._check, name="gid-update-checker", daemon=True).start()

    def check_sync(self):
        return self._check()

    @property
    def update_available(self):
        if not self._latest or not pkg_version:
            return False
        try:
            return pkg_version.parse(self._latest) > pkg_version.parse(self._current)
        except Exception:
            return False

    @property
    def latest_version(self):
        return self._latest

    def _check(self):
        try:
            import requests
            r = requests.get(self._check_url, timeout=10, headers={"Accept": "application/vnd.github.v3+json"})
            if r.status_code != 200:
                return None
            data = r.json()
            self._latest = data.get("tag_name", "").lstrip("v")
            self._download_url = data.get("html_url", "")
            if self.update_available and self._on_update:
                self._on_update(self._latest, self._download_url)
            return {"current": self._current, "latest": self._latest, "download_url": self._download_url, "update_available": self.update_available}
        except Exception as e:
            logger.debug("Update check failed: %s", e)
        return None
