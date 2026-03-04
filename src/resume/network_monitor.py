"""GrabItDown network monitor — detects connectivity changes and notifies the download engine."""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

NetworkState = str  # "connected" | "disconnected"


class NetworkMonitor:
    """Monitors network connectivity and fires callbacks on state changes."""

    DEFAULT_CHECK_HOSTS: List[Tuple[str, int]] = [
        ("www.google.com", 443),
        ("www.cloudflare.com", 443),
        ("1.1.1.1", 443),
    ]

    def __init__(
        self,
        check_interval: float = 5.0,
        timeout: float = 3.0,
        check_hosts: Optional[List[Tuple[str, int]]] = None,
        stabilization_delay: float = 3.0,
    ) -> None:
        """Initialize network monitor."""
        self._check_interval = check_interval
        self._timeout = timeout
        self._check_hosts = check_hosts or self.DEFAULT_CHECK_HOSTS
        self._stabilization_delay = stabilization_delay

        self._state: NetworkState = "connected"
        self._lock = threading.Lock()

        self._on_connected: List[Callable[[], None]] = []
        self._on_disconnected: List[Callable[[], None]] = []

        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def is_connected(self) -> bool:
        """Current connectivity state."""
        with self._lock:
            return self._state == "connected"

    @property
    def state(self) -> NetworkState:
        """Return current state string."""
        with self._lock:
            return self._state

    def on_connected(self, callback: Callable[[], None]) -> None:
        """Register callback for when connection is restored."""
        self._on_connected.append(callback)

    def on_disconnected(self, callback: Callable[[], None]) -> None:
        """Register callback for when connection is lost."""
        self._on_disconnected.append(callback)

    def start(self) -> None:
        """Start monitoring in background thread."""
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="gid-network-monitor",
            daemon=True,
        )
        self._monitor_thread.start()
        logger.info("Network monitor started")

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=10)
        logger.info("Network monitor stopped")

    def check_now(self) -> bool:
        """Perform an immediate connectivity check."""
        return self._check_connectivity()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                is_online = self._check_connectivity()

                with self._lock:
                    previous = self._state
                    new_state: NetworkState = "connected" if is_online else "disconnected"

                if previous != new_state:
                    if new_state == "connected":
                        logger.info(
                            "Connection detected, waiting for stabilization (%ss)",
                            self._stabilization_delay,
                        )
                        time.sleep(self._stabilization_delay)

                        if self._check_connectivity():
                            with self._lock:
                                self._state = "connected"
                            self._fire_callbacks(self._on_connected)
                            logger.info("Network: CONNECTED")
                    else:
                        with self._lock:
                            self._state = "disconnected"
                        self._fire_callbacks(self._on_disconnected)
                        logger.warning("Network: DISCONNECTED")

            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Monitor error: %s", exc)

            time.sleep(self._check_interval)

    def _check_connectivity(self) -> bool:
        """Try to connect to check hosts; True if any is reachable."""
        for host, port in self._check_hosts:
            try:
                sock = socket.create_connection((host, port), timeout=self._timeout)
                sock.close()
                return True
            except OSError:
                continue
        return False

    def _fire_callbacks(self, callbacks: List[Callable[[], None]]) -> None:
        """Fire all callbacks, catching exceptions."""
        for cb in callbacks:
            try:
                cb()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Network callback error: %s", exc)

