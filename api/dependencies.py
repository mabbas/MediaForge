"""FastAPI dependency injection."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException

from src.grabitdown import GrabItDown

_app_instance: GrabItDown | None = None


def get_app() -> GrabItDown:
    """Get the GrabItDown app instance (singleton)."""
    global _app_instance
    if _app_instance is None:
        _app_instance = GrabItDown()
        _app_instance.start()

        # Wire progress bridge to engine
        from api.services.progress_bridge import get_progress_bridge

        bridge = get_progress_bridge()
        _app_instance._engine.progress_tracker.add_listener(bridge.on_progress)

        # Wire DB sync to engine
        from api.services.engine_sync import get_engine_sync

        db_sync = get_engine_sync()
        _app_instance._engine.progress_tracker.add_listener(db_sync.on_progress)

    return _app_instance


def get_registry():
    return get_app()._registry


def get_engine():
    return get_app()._engine


def get_feature_gate():
    return get_app()._gate


def get_usage_tracker():
    return get_app()._usage


async def shutdown_app() -> None:
    """Shut down the GrabItDown app singleton."""
    global _app_instance
    if _app_instance:
        _app_instance.shutdown(wait=True)
        _app_instance = None


# ── Auth Dependencies (Skeleton) ──


class CurrentUser:
    """Represents the current authenticated user.

    In personal mode, returns a default system user.
    In tiered/enterprise mode, resolves from JWT.
    """

    def __init__(
        self,
        user_id: str = "system",
        tenant_id: str = "default",
        email: str | None = None,
        role: str = "owner",
        tier: str = "platinum",
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.email = email
        self.role = role
        self.tier = tier


async def get_current_user() -> CurrentUser:
    """Get current user.

    CURRENT: Returns default system user (personal mode).
    FUTURE: Parse JWT token, validate, return real user.
    """
    from src.features.feature_flags import load_feature_flags

    flags = load_feature_flags()

    if flags.mode == "personal":
        # Use seeded system user/tenant IDs so download_jobs FK is satisfied
        return CurrentUser(
            user_id="00000000-0000-0000-0000-000000000002",
            tenant_id="00000000-0000-0000-0000-000000000001",
            role="owner",
            tier=flags.personal_mode_tier,
        )

    raise HTTPException(
        status_code=401,
        detail="Authentication required in tiered mode",
    )


def require_tier(min_tier: str):
    """Dependency factory for tier-based access.

    CURRENT: Always passes in personal mode.
    FUTURE: Check user's tier meets minimum.
    """

    async def _check(user: CurrentUser = Depends(get_current_user)):
        tier_levels = {"basic": 0, "pro": 1, "platinum": 2}
        user_level = tier_levels.get(user.tier, 0)
        required_level = tier_levels.get(min_tier, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail=f"Requires {min_tier} tier or higher",
            )
        return user

    return _check
