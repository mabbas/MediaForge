"""WebSocket endpoints for real-time progress updates with secure authentication (STEP 02-B1)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

import api.database.connection as conn_mod
from api.database.connection import get_session
from api.dependencies import CurrentUser, get_current_user, resolve_principal_from_token
from api.services.progress_bridge import get_progress_bridge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])


async def _get_ws_session(websocket: WebSocket) -> Any:
    """Resolve an AsyncSession for WebSocket authentication, honoring test overrides."""
    override = websocket.app.dependency_overrides.get(get_session)
    if override:
        gen = override()
        if hasattr(gen, "__anext__"):
            return await gen.__anext__()
        elif hasattr(gen, "__next__"):
            return next(gen)
        return gen
    if conn_mod.async_session_factory:
        return conn_mod.async_session_factory()
    raise RuntimeError("Database session factory unavailable for WebSocket auth")


@router.websocket("/progress")
async def ws_progress(websocket: WebSocket) -> None:
    """Accept WebSocket connections for download progress updates.

    Authentication Security:
    - Primary (recommended) vector: Initial message {"type": "auth", "token": "<jwt>"}
      Avoids credential leakage in URL query strings and web server access logs.
    - Fallback vector: Query parameter ?token=<jwt> for simple clients.
    - Validates cryptographic signature and authoritative database identity.
    - Rejects unauthenticated, expired, or invalid connections with RFC 6455 code 1008 (Policy Violation).
    """
    await websocket.accept()

    principal: CurrentUser | None = None
    override = websocket.app.dependency_overrides.get(get_current_user)

    if override:
        principal = override() if callable(override) else override
    else:
        token: str | None = websocket.query_params.get("token")
        if not token:
            # Wait up to 2.0s for the preferred in-band authentication message
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                msg = json.loads(raw)
                if isinstance(msg, dict) and msg.get("type") == "auth":
                    token = msg.get("token")
            except Exception:
                pass

        if token and isinstance(token, str) and token.strip():
            try:
                session = await _get_ws_session(websocket)
                try:
                    principal = await resolve_principal_from_token(token.strip(), session)
                finally:
                    if hasattr(session, "close"):
                        res = session.close()
                        if asyncio.iscoroutine(res):
                            await res
            except Exception as exc:
                logger.debug("WebSocket token verification failed: %s", exc)
                principal = None

    if not principal:
        await websocket.close(code=1008, reason="Authentication required")
        return

    # Authenticated session active
    bridge = get_progress_bridge()
    bridge.register_client(
        websocket,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        role=principal.role,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if isinstance(msg, dict):
                    _type = msg.get("type")
                    if _type == "subscribe_all":
                        bridge.subscribe(websocket, subscribe_all=True)
                    elif _type == "subscribe" and "job_ids" in msg:
                        jids = msg.get("job_ids")
                        if isinstance(jids, list):
                            bridge.subscribe(websocket, job_ids=jids)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket progress closed: %s", e)
    finally:
        bridge.unregister_client(websocket)
