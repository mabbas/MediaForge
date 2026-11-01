"""Progress bridge — forwards engine progress to WebSocket/broadcast.

Runs in a background thread with its own asyncio loop (same pattern
as EngineDatabaseSync). Listens to engine progress_tracker and can
push updates to WebSocket clients.
"""

from __future__ import annotations

from typing import Any

import asyncio
import logging
import threading

from src.models.download import DownloadProgress

logger = logging.getLogger(__name__)


class ProgressBridge:
    """Bridges engine progress to async consumers (e.g. WebSocket)."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._subscribers: dict[object, dict[str, Any]] = {}
        self._subscribers_lock = threading.Lock()
        self._app: Any = None

    def set_app(self, app: Any) -> None:
        """Inject app instance to eliminate circular imports with dependencies.py."""
        self._app = app

    def set_main_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the main app event loop so WebSocket broadcasts run there."""
        self._main_loop = loop

    def register_client(
        self,
        ws: object,
        tenant_id: str,
        user_id: str | None = None,
        role: str = "member",
    ) -> None:
        """Register an authenticated WebSocket client with its tenant boundary."""
        with self._subscribers_lock:
            self._subscribers[ws] = {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "role": role,
                "subscribed_all": False,
                "job_ids": set(),
            }

    def unregister_client(self, ws: object) -> None:
        """Remove a disconnected WebSocket client."""
        with self._subscribers_lock:
            self._subscribers.pop(ws, None)

    def subscribe(
        self,
        ws: object,
        job_ids: list[str] | None = None,
        subscribe_all: bool = False,
    ) -> None:
        """Update subscription filters for an active client."""
        with self._subscribers_lock:
            if ws in self._subscribers:
                if subscribe_all:
                    self._subscribers[ws]["subscribed_all"] = True
                if job_ids:
                    self._subscribers[ws]["job_ids"].update(job_ids)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._main_loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="gid-progress-bridge",
                daemon=True,
            )
            self._thread.start()
        logger.info("Progress bridge started")

    def stop(self) -> None:
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        with self._subscribers_lock:
            self._subscribers.clear()
        logger.info("Progress bridge stopped")

    def on_progress(self, job_id: str, progress: DownloadProgress) -> None:
        """Called from download engine worker threads."""
        target_loop = self._main_loop if self._main_loop is not None else self._loop
        if not self._running or target_loop is None:
            return

        asyncio.run_coroutine_threadsafe(
            self._broadcast(job_id, progress),
            target_loop,
        )

    async def _broadcast(self, job_id: str, progress: DownloadProgress) -> None:
        """Broadcast progress to WebSocket clients belonging strictly to the job's tenant."""
        app = self._app
        if app is None:
            try:
                from api.dependencies import get_app
                app = get_app()
            except Exception:
                return
        job = app.get_job(job_id)
        if not job:
            return

        job_tenant = getattr(job, "tenant_id", None)
        job_user = getattr(job, "user_id", None)

        with self._subscribers_lock:
            snapshot = list(self._subscribers.items())

        if not snapshot:
            return

        status_val = (
            progress.status.value
            if hasattr(progress.status, "value")
            else str(progress.status)
        )
        msg = {
            "type": "progress",
            "job_id": job_id,
            "status": status_val,
            "percent": progress.percent,
            "bytes_downloaded": progress.bytes_downloaded,
            "total_bytes": progress.total_bytes,
            "speed": progress.speed_human,
            "eta": progress.eta_human,
        }

        for ws, sub in snapshot:
            # Strict tenant isolation boundary:
            if sub.get("tenant_id") != job_tenant:
                continue

            # RBAC isolation: regular members only see their own job progress
            if sub.get("role") == "member" and sub.get("user_id") != job_user:
                continue

            # Subscription filter
            if not sub.get("subscribed_all") and job_id not in sub.get("job_ids", set()):
                continue

            try:
                if hasattr(ws, "send_json"):
                    await ws.send_json(msg)
                elif hasattr(ws, "send_text"):
                    import json

                    await ws.send_text(json.dumps(msg))
            except Exception:
                pass

    def _run_loop(self) -> None:
        if self._loop:
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()


_bridge: ProgressBridge | None = None


def get_progress_bridge() -> ProgressBridge:
    global _bridge
    if _bridge is None:
        _bridge = ProgressBridge()
    return _bridge


def reset_progress_bridge() -> None:
    global _bridge
    if _bridge:
        _bridge.stop()
    _bridge = None
