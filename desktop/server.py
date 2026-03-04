"""Embedded FastAPI server for desktop app.

Runs uvicorn in a background thread so the main
thread can run the native window.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import uvicorn

logger = logging.getLogger(__name__)


class EmbeddedServer:
    """Runs FastAPI in a background thread.

    The desktop app needs:
    1. Main thread → pywebview window (GUI)
    2. Background thread → uvicorn/FastAPI (server)

    This class manages the server lifecycle.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        database_url: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None
        self._started = threading.Event()
        self._running = False

        # Configure environment for embedded mode
        os.environ["GID_API_HOST"] = host
        os.environ["GID_API_PORT"] = str(port)
        os.environ["GID_API_CORS_ORIGINS"] = '["*"]'
        os.environ["GID_API_RATE_LIMIT_PER_MINUTE"] = "300"

        if database_url:
            os.environ["GID_API_DATABASE_URL"] = database_url

    def start(self, timeout: float = 15.0) -> bool:
        """Start the server in a background thread.

        Returns True if server started successfully.
        Blocks until server is ready or timeout.
        """
        if self._running:
            return True

        self._running = True
        self._thread = threading.Thread(
            target=self._run_server,
            name="gid-embedded-server",
            daemon=True,
        )
        self._thread.start()

        # Wait for server to be ready
        started = self._started.wait(timeout=timeout)
        if started:
            logger.info(
                "Embedded server ready at http://%s:%s",
                self.host,
                self.port,
            )
        else:
            logger.error("Server failed to start within %ss", timeout)

        return started

    def stop(self) -> None:
        """Stop the embedded server."""
        self._running = False
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Embedded server stopped")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v1"

    @property
    def is_running(self) -> bool:
        return self._running and self._started.is_set()

    def _run_server(self) -> None:
        """Run uvicorn in this thread."""
        try:
            config = uvicorn.Config(
                "api.main:app",
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
                workers=1,
            )
            server = uvicorn.Server(config)
            self._server = server

            original_startup = server.startup

            async def patched_startup(*args, **kwargs):
                result = await original_startup(*args, **kwargs)
                self._started.set()
                return result

            server.startup = patched_startup  # type: ignore[assignment]
            server.run()

        except Exception as e:  # pragma: no cover - defensive
            logger.error("Server error: %s", e)
            # Ensure waiters are unblocked even on failure
            self._started.set()

