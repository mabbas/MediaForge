"""GrabItDown Desktop Application — main entry point.

Architecture:
  1. Start embedded FastAPI server (background thread)
  2. Open native window with pywebview
  3. Load built-in dashboard (or React UI if built)
  4. Window events → server control
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

# Add project root to path and load .env (so GID_FFMPEG_LOCATION etc. are available)
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from src.env_loader import load_project_dotenv
    load_project_dotenv()
except Exception:  # noqa: BLE001
    pass

from desktop.bridge import JSBridge
from desktop.clipboard import ClipboardMonitor
from desktop.config import get_desktop_settings
from desktop.crash_handler import install_crash_handler
from desktop.dashboard import get_dashboard_html
from desktop.dialogs import NativeDialogs
from desktop.hotkeys import HotkeyManager
from desktop.instance_lock import InstanceLock
from desktop.notifications import NotificationManager
from desktop.persistence import (
    load_desktop_prefs,
    load_window_state,
    save_window_state,
)
from desktop.server import EmbeddedServer
from desktop.tray import SystemTray
from desktop.updater import UpdateChecker

logger = logging.getLogger(__name__)


class GrabItDownDesktop:
    """Main desktop application class."""

    def __init__(self):
        self.settings = get_desktop_settings()
        self.server: EmbeddedServer | None = None
        self.window = None
        self.tray: SystemTray | None = None
        self.dialogs: NativeDialogs | None = None
        self.clipboard: ClipboardMonitor | None = None
        self.hotkeys: HotkeyManager | None = None
        self.notifications: NotificationManager | None = None
        self.bridge: JSBridge | None = None
        self.lock: InstanceLock | None = None
        self._setup_logging()
        self._setup_directories()

    def _setup_logging(self) -> None:
        log_dir = Path(self.settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.FileHandler(log_dir / "desktop.log"),
                logging.StreamHandler(),
            ],
        )

    def _setup_directories(self) -> None:
        data_dir = Path(self.settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "downloads").mkdir(exist_ok=True)
        (data_dir / "data").mkdir(exist_ok=True)

    def _register_dashboard_route(self) -> None:
        """Register GET /dashboard and GET /transcripts on the API app."""
        from fastapi.responses import HTMLResponse

        from api.main import app as fastapi_app
        from desktop.dashboard import get_dashboard_html, get_transcripts_html

        base_url = (
            f"http://{self.settings.server_host}:{self.settings.server_port}"
        )

        @fastapi_app.get("/dashboard", include_in_schema=False)
        def _serve_dashboard() -> HTMLResponse:
            return HTMLResponse(get_dashboard_html(base_url))

        @fastapi_app.get("/transcripts", include_in_schema=False)
        def _serve_transcripts() -> HTMLResponse:
            return HTMLResponse(get_transcripts_html(base_url))

    def run(self) -> None:
        """Start the desktop application."""
        logger.info("GrabItDown Desktop starting...")

        install_crash_handler(self.settings.log_dir)

        self.lock = InstanceLock(self.settings.data_dir)
        if not self.lock.acquire():
            print("GrabItDown is already running.")
            print("Check system tray for the running instance.")
            sys.exit(0)

        saved_prefs = load_desktop_prefs(self.settings.data_dir)
        if saved_prefs:
            if "check_clipboard" in saved_prefs:
                setattr(
                    self.settings,
                    "check_clipboard",
                    saved_prefs["check_clipboard"],
                )
            if "show_notifications" in saved_prefs:
                setattr(
                    self.settings,
                    "show_notifications",
                    saved_prefs["show_notifications"],
                )
            if "minimize_to_tray" in saved_prefs:
                setattr(
                    self.settings,
                    "minimize_to_tray",
                    saved_prefs["minimize_to_tray"],
                )

        # Determine database URL (SQLite for desktop)
        db_path = Path(self.settings.data_dir) / "data" / "grabitdown.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"

        # Register /dashboard route so fallback browser opens the UI (not /docs)
        self._register_dashboard_route()

        # Start embedded server
        self.server = EmbeddedServer(
            host=self.settings.server_host,
            port=self.settings.server_port,
            database_url=db_url,
        )

        if not self.server.start(timeout=15):
            logger.error("Failed to start embedded server")
            print("ERROR: Could not start server. Check logs.")
            sys.exit(1)

        logger.info("Server running at %s", self.server.base_url)

        from desktop.version import APP_VERSION

        updater = UpdateChecker(
            current_version=APP_VERSION,
            on_update_available=self._on_update_available,
        )
        updater.check_async()

        # Generate dashboard HTML
        html = get_dashboard_html(self.server.base_url)

        # Start native window (pywebview; on Windows uses WebView2 when available)
        try:
            import webview

            self.bridge = JSBridge(app=self)
            saved_state = load_window_state(self.settings.data_dir)
            width = self.settings.window_width
            height = self.settings.window_height
            x = None
            y = None
            if saved_state:
                width = saved_state.get("width", width)
                height = saved_state.get("height", height)
                x = saved_state.get("x")
                y = saved_state.get("y")

            self.window = webview.create_window(
                title=self.settings.window_title,
                html=html,
                js_api=self.bridge,
                width=width,
                height=height,
                x=x,
                y=y,
                min_size=(
                    self.settings.window_min_width,
                    self.settings.window_min_height,
                ),
                resizable=True,
                on_top=self.settings.always_on_top,
                text_select=True,
            )

            # Register window events
            self.window.events.closed += self._on_close  # type: ignore[attr-defined]
            if hasattr(self.window.events, "closing"):
                self.window.events.closing += self._on_closing  # type: ignore[attr-defined]

            # Setup system tray
            if self.settings.minimize_to_tray:
                self.tray = SystemTray(
                    on_show=self._show_window,
                    on_quit=self._quit_app,
                    on_pause=self._pause_downloads,
                    on_resume=self._resume_downloads,
                )
                self.tray.start()

            # Setup dialogs
            self.dialogs = NativeDialogs(window=self.window)

            # Setup notifications
            self.notifications = NotificationManager(
                tray=self.tray
            )
            self.notifications.enabled = (
                self.settings.show_notifications
            )

            # Setup clipboard monitor
            if self.settings.check_clipboard:
                self.clipboard = ClipboardMonitor(
                    on_url_detected=self._on_clipboard_url,
                )
                self.clipboard.start()

            # Setup hotkeys
            self.hotkeys = HotkeyManager()
            if self.hotkeys.is_available:
                self.hotkeys.register(
                    "ctrl+shift+d",
                    self._quick_download,
                    "Quick download from clipboard",
                )
                self.hotkeys.register(
                    "ctrl+shift+p",
                    self._toggle_pause,
                    "Pause/Resume downloads",
                )

            # Start tray status updater
            self._start_tray_updater()

            logger.info("Opening desktop window...")
            # On Windows, prefer Edge WebView2 (Chromium) for best compatibility
            start_kwargs = {
                "debug": os.environ.get("GID_DEBUG", "").lower() == "true",
            }
            if sys.platform == "win32":
                start_kwargs["gui"] = "edgechromium"
            webview.start(**start_kwargs)

        except ImportError as e:
            logger.warning("pywebview not installed (%s). Opening in browser.", e)
            import webbrowser
            webbrowser.open(self.server.base_url.rstrip("/") + "/dashboard")
            print()
            print("GrabItDown is running in your browser (pywebview not installed).")
            print("Install the desktop window:  pip install pywebview")
            print(f"Dashboard: {self.server.base_url.rstrip('/')}/dashboard")
            print("Press Ctrl+C to stop.")
            print()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        except Exception as e:
            err_msg = str(e).lower()
            if (
                "pythonnet" in err_msg
                or "no module named 'clr'" in err_msg
                or ("webview" in err_msg and ("must have" in err_msg or "cannot be loaded" in err_msg))
            ):
                logger.warning(
                    "Native window unavailable (%s). Opening in browser.",
                    e,
                )
                import webbrowser
                webbrowser.open(self.server.base_url.rstrip("/") + "/dashboard")
                print()
                print("GrabItDown is running in your browser (native window requires pythonnet).")
                print("Dashboard: " + (self.server.base_url.rstrip("/") + "/dashboard"))
                print("Press Ctrl+C to stop.")
                print()
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass
            else:
                raise
        except (OSError, RuntimeError) as e:
            logger.warning(
                "Native window unavailable (%s). Opening in browser instead.",
                e,
                exc_info=True,
            )
            import webbrowser
            webbrowser.open(self.server.base_url.rstrip("/") + "/dashboard")
            print()
            print("GrabItDown is running in your browser (native window unavailable).")
            print()
            if sys.platform == "win32":
                print("On Windows, install WebView2 Runtime for the desktop window:")
                print("  https://developer.microsoft.com/en-us/microsoft-edge/webview2/")
                print("  (Often already installed with Microsoft Edge.)")
            else:
                print("Ensure your system has a supported GUI backend for pywebview.")
            print()
            print(f"Dashboard: {self.server.base_url.rstrip('/')}/dashboard")
            print("Press Ctrl+C to stop.")
            print()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        finally:
            self._shutdown()

    def _on_closing(self) -> None:
        """Before window close: minimize to tray if enabled."""
        if (
            self.settings.minimize_to_tray
            and self.tray
            and self.window
        ):
            try:
                self.window.hide()
            except Exception:
                pass

    def _on_close(self) -> None:
        """Handle window close: save state."""
        if self.window:
            try:
                save_window_state(
                    {
                        "width": self.window.width,
                        "height": self.window.height,
                        "x": getattr(self.window, "x", None),
                        "y": getattr(self.window, "y", None),
                    },
                    self.settings.data_dir,
                )
            except Exception:
                pass
        logger.info("Window closed")

    def _on_update_available(self, version: str, url: str) -> None:
        if self.notifications:
            self.notifications.notify(
                "Update Available",
                f"GrabItDown {version} is available. Visit the website to update.",
                "info",
            )

    def _show_window(self) -> None:
        """Show and restore main window (from tray)."""
        if self.window:
            self.window.show()
            self.window.restore()

    def _quit_app(self) -> None:
        """Quit from tray; destroys window and triggers shutdown."""
        if self.window:
            self.window.destroy()

    def _pause_downloads(self) -> None:
        """Pause download queue via API."""
        if not self.server:
            return
        try:
            import requests

            requests.post(
                f"{self.server.api_url}/queue/pause",
                timeout=5,
            )
        except Exception:
            pass

    def _resume_downloads(self) -> None:
        """Resume download queue via API."""
        if not self.server:
            return
        try:
            import requests

            requests.post(
                f"{self.server.api_url}/queue/resume",
                timeout=5,
            )
        except Exception:
            pass

    def _on_clipboard_url(self, url: str) -> None:
        """Handle detected media URL in clipboard."""
        if self.notifications:
            self.notifications.clipboard_url_detected(url)
        logger.info("Clipboard URL: %s", url[:80])

    def _quick_download(self) -> None:
        """Download whatever is in clipboard."""
        if not self.server:
            return
        try:
            from desktop.clipboard import is_media_url

            try:
                import pyperclip
            except ImportError:
                return
            url = pyperclip.paste()
            if url and is_media_url(url.strip()):
                import requests

                r = requests.post(
                    f"{self.server.api_url}/downloads",
                    json={
                        "url": url.strip(),
                        "mode": "video",
                        "quality": "1080p",
                    },
                    timeout=5,
                )
                if r.status_code == 202 and self.notifications:
                    self.notifications.download_started(
                        "Clipboard Download", url
                    )
        except Exception as e:
            logger.warning("Quick download failed: %s", e)

    def _toggle_pause(self) -> None:
        """Toggle pause/resume via hotkey."""
        if not self.server:
            return
        try:
            import requests

            stats = requests.get(
                f"{self.server.api_url}/queue/stats",
                timeout=3,
            ).json()
            if stats.get("is_paused"):
                requests.post(
                    f"{self.server.api_url}/queue/resume",
                    timeout=3,
                )
                if self.notifications:
                    self.notifications.notify(
                        "Downloads Resumed",
                        "Queue resumed",
                    )
            else:
                requests.post(
                    f"{self.server.api_url}/queue/pause",
                    timeout=3,
                )
                if self.notifications:
                    self.notifications.notify(
                        "Downloads Paused",
                        "Queue paused",
                    )
        except Exception:
            pass

    def _start_tray_updater(self) -> None:
        """Update tray status every 5 seconds."""
        if not self.tray:
            return

        def _updater() -> None:
            try:
                import requests
            except ImportError:
                return
            while self.server and self.server.is_running:
                try:
                    r = requests.get(
                        f"{self.server.api_url}/queue/stats",
                        timeout=3,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        queue = data.get("queue") or {}
                        queued = queue.get("total", 0)
                        self.tray.update_status(
                            active=data.get("active", 0),
                            queued=queued,
                            is_paused=data.get(
                                "is_paused", False
                            ),
                        )
                except Exception:
                    pass
                time.sleep(5)

        t = threading.Thread(
            target=_updater,
            name="gid-tray-updater",
            daemon=True,
        )
        t.start()

    def _shutdown(self) -> None:
        """Clean shutdown."""
        logger.info("GrabItDown Desktop shutting down...")
        if self.lock:
            self.lock.release()
        if self.clipboard:
            self.clipboard.stop()
        if self.hotkeys:
            self.hotkeys.unregister_all()
        if self.tray:
            self.tray.stop()
        if self.server:
            self.server.stop()
        logger.info("GrabItDown Desktop stopped")


def main() -> None:
    """Entry point for desktop app."""
    app = GrabItDownDesktop()
    app.run()


if __name__ == "__main__":
    main()

