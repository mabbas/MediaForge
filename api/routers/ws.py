"""WebSocket endpoints for real-time progress."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.services.progress_bridge import get_progress_bridge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/progress")
async def ws_progress(websocket: WebSocket) -> None:
    """Accept WebSocket connections for download progress updates.

    Client can send: {"type": "subscribe_all"} or {"type": "subscribe", "job_ids": [...]}.
    Server sends progress/status_change messages when bridge is wired.
    """
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                _type = msg.get("type")
                if _type == "subscribe_all":
                    pass  # Bridge would add to broadcast list
                elif _type == "subscribe" and "job_ids" in msg:
                    pass  # Bridge would add job_ids to this connection
            except json.JSONDecodeError:
                pass
            # Keep connection alive; progress bridge can push via get_progress_bridge()
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket progress closed: %s", e)
