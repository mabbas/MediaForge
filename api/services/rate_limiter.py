"""Redis-backed rate limiting and cooldown service (STEP 02-B2A & STEP 02-B2B).

Provides fixed-window rate limiting (with TTL initialized on first increment) and
cooldown enforcement for sensitive authentication endpoints (registration, login, resend-verification).

Security Policy:
- Sensitive public endpoints fail closed with RateLimiterUnavailableError (HTTP 503)
  when Redis rate limiting cannot be enforced, preventing brute-force attacks during Redis outages.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Optional

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore

from api.config import get_api_settings

logger = logging.getLogger(__name__)

_redis_client: Optional[aioredis.Redis] = None
_redis_client_loop: Optional[asyncio.AbstractEventLoop] = None


class RateLimiterUnavailableError(Exception):
    """Raised when the rate limiter backend is unavailable and fail-closed policy is active."""


async def get_redis_client() -> Optional[aioredis.Redis]:
    """Get or create singleton Redis client bound to the current event loop."""
    global _redis_client, _redis_client_loop
    if aioredis is None:
        return None

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    # Reset client if previous event loop is closed or different
    if _redis_client is not None and (
        _redis_client_loop is None
        or _redis_client_loop.is_closed()
        or _redis_client_loop is not current_loop
    ):
        _redis_client = None
        _redis_client_loop = None

    if _redis_client is None:
        settings = get_api_settings()
        try:
            _redis_client = aioredis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                password=settings.redis_password or None,
                db=settings.redis_db,
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            # Test connectivity
            await _redis_client.ping()
            _redis_client_loop = current_loop
        except Exception as exc:
            logger.warning(
                "Redis connection failed (%s). Rate limiter offline.",
                exc,
            )
            _redis_client = None
            _redis_client_loop = None

    return _redis_client


async def close_redis_client() -> None:
    """Close Redis client connection."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception as exc:
            logger.debug("Error closing Redis client: %s", exc)
        finally:
            _redis_client = None


def hash_key_identifier(identifier: str) -> str:
    """Hash identifier (e.g. email) with SHA-256 to avoid storing PII in Redis keys."""
    return hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()[:16]


_SINGLE_RATE_LIMIT_LUA = """
local metric = KEYS[1]
local window = tonumber(ARGV[1])

local count = redis.call('INCR', metric)
if count == 1 then
    redis.call('EXPIRE', metric, window)
end
local ttl = redis.call('TTL', metric)
if ttl == -1 then
    redis.call('EXPIRE', metric, window)
    ttl = window
end

return {count, ttl}
"""

_DUAL_RATE_LIMIT_LUA = """
local key_metric = KEYS[1]
local tenant_metric = KEYS[2]
local window = tonumber(ARGV[1])

local k_count = redis.call('INCR', key_metric)
if k_count == 1 then
    redis.call('EXPIRE', key_metric, window)
end
local k_ttl = redis.call('TTL', key_metric)
if k_ttl == -1 then
    redis.call('EXPIRE', key_metric, window)
    k_ttl = window
end

local t_count = redis.call('INCR', tenant_metric)
if t_count == 1 then
    redis.call('EXPIRE', tenant_metric, window)
end
local t_ttl = redis.call('TTL', tenant_metric)
if t_ttl == -1 then
    redis.call('EXPIRE', tenant_metric, window)
    t_ttl = window
end

return {k_count, k_ttl, t_count, t_ttl}
"""


class RateLimiter:
    """Rate limiter with Redis backend and fail-closed security enforcement."""

    @staticmethod
    async def check_limit(
        key: str,
        limit: int,
        window_seconds: int,
        fail_closed: Optional[bool] = None,
    ) -> tuple[bool, int, int]:
        """Check if request count is within limit for the given window.

        Window algorithm: Fixed window initialized with expiry upon first key creation.
        Uses atomic Redis Lua evaluation to guarantee positive TTL initialization without races.

        Returns:
            (allowed: bool, current_count: int, retry_after: int)

        Raises:
            RateLimiterUnavailableError: If Redis is unavailable and fail_closed is True.
        """
        settings = get_api_settings()
        enforce_fail_closed = (
            fail_closed if fail_closed is not None else settings.rate_limit_fail_closed
        )

        try:
            client = await get_redis_client()
            if client is None:
                if enforce_fail_closed:
                    raise RateLimiterUnavailableError("Rate limiter backend unavailable")
                return True, 0, 0

            results = await client.eval(_SINGLE_RATE_LIMIT_LUA, 1, key, window_seconds)
            count = results[0]
            ttl = results[1]

            if count > limit:
                retry_after = max(ttl, 1)
                return False, count, retry_after

            return True, count, 0

        except RateLimiterUnavailableError:
            raise
        except Exception as exc:
            logger.warning("Redis rate limiter error for key %s: %s", key, exc)
            if enforce_fail_closed:
                raise RateLimiterUnavailableError("Rate limiter backend error") from exc
            return True, 0, 0

    @staticmethod
    async def check_cooldown(
        key: str,
        cooldown_seconds: int,
        fail_closed: Optional[bool] = None,
    ) -> tuple[bool, int]:
        """Enforce cooldown interval between actions.

        Returns:
            (allowed: bool, retry_after: int)

        Raises:
            RateLimiterUnavailableError: If Redis is unavailable and fail_closed is True.
        """
        settings = get_api_settings()
        enforce_fail_closed = (
            fail_closed if fail_closed is not None else settings.rate_limit_fail_closed
        )

        try:
            client = await get_redis_client()
            if client is None:
                if enforce_fail_closed:
                    raise RateLimiterUnavailableError("Rate limiter backend unavailable")
                return True, 0

            # SETNX with expiration
            set_success = await client.set(key, "1", ex=cooldown_seconds, nx=True)
            if set_success:
                return True, 0

            ttl = await client.ttl(key)
            return False, max(ttl, 1)

        except RateLimiterUnavailableError:
            raise
        except Exception as exc:
            logger.warning("Redis cooldown error for key %s: %s", key, exc)
            if enforce_fail_closed:
                raise RateLimiterUnavailableError("Rate limiter backend error") from exc
            return True, 0

    @classmethod
    async def check_registration_ip(
        cls, ip: str, fail_closed: Optional[bool] = None
    ) -> tuple[bool, int]:
        """Check per-IP registration rate limit."""
        settings = get_api_settings()
        key = f"rl:reg:ip:{ip}"
        allowed, _, retry_after = await cls.check_limit(
            key=key,
            limit=settings.registration_rate_limit_per_minute,
            window_seconds=60,
            fail_closed=fail_closed,
        )
        return allowed, retry_after

    @classmethod
    async def check_registration_email(
        cls, email: str, fail_closed: Optional[bool] = None
    ) -> tuple[bool, int]:
        """Check per-email registration attempt rate limit."""
        settings = get_api_settings()
        key = f"rl:reg:email:{hash_key_identifier(email)}"
        allowed, _, retry_after = await cls.check_limit(
            key=key,
            limit=settings.registration_rate_limit_per_hour_email,
            window_seconds=3600,
            fail_closed=fail_closed,
        )
        return allowed, retry_after

    @classmethod
    async def check_resend_cooldown(
        cls, email: str, fail_closed: Optional[bool] = None
    ) -> tuple[bool, int]:
        """Check resend verification cooldown for an email."""
        settings = get_api_settings()
        key = f"rl:reg:resend:{hash_key_identifier(email)}"
        return await cls.check_cooldown(
            key=key,
            cooldown_seconds=settings.resend_cooldown_seconds,
            fail_closed=fail_closed,
        )

    @classmethod
    async def check_login_ip(
        cls, ip: str, fail_closed: Optional[bool] = None
    ) -> tuple[bool, int]:
        """Check per-IP login attempt rate limit."""
        settings = get_api_settings()
        key = f"rl:login:ip:{ip}"
        allowed, _, retry_after = await cls.check_limit(
            key=key,
            limit=settings.login_rate_limit_per_minute,
            window_seconds=60,
            fail_closed=fail_closed,
        )
        return allowed, retry_after

    @classmethod
    async def check_login_email(
        cls, email: str, fail_closed: Optional[bool] = None
    ) -> tuple[bool, int]:
        """Check per-email login attempt rate limit."""
        settings = get_api_settings()
        key = f"rl:login:email:{hash_key_identifier(email)}"
        allowed, _, retry_after = await cls.check_limit(
            key=key,
            limit=settings.login_rate_limit_per_15min_email,
            window_seconds=900,
            fail_closed=fail_closed,
        )
        return allowed, retry_after

    @classmethod
    async def check_password_reset_ip(
        cls, ip: str, fail_closed: Optional[bool] = None
    ) -> tuple[bool, int]:
        """Check per-IP password reset request rate limit."""
        settings = get_api_settings()
        key = f"rl:pwdreset:ip:{ip}"
        allowed, _, retry_after = await cls.check_limit(
            key=key,
            limit=settings.password_reset_rate_limit_per_minute,
            window_seconds=60,
            fail_closed=fail_closed,
        )
        return allowed, retry_after

    @classmethod
    async def check_password_reset_email(
        cls, email: str, fail_closed: Optional[bool] = None
    ) -> tuple[bool, int]:
        """Check per-email password reset request rate limit."""
        settings = get_api_settings()
        key = f"rl:pwdreset:email:{hash_key_identifier(email)}"
        allowed, _, retry_after = await cls.check_limit(
            key=key,
            limit=settings.password_reset_rate_limit_per_hour_email,
            window_seconds=3600,
            fail_closed=fail_closed,
        )
        return allowed, retry_after

    @classmethod
    async def check_api_key_limits(
        cls,
        key_id: str,
        tenant_id: str,
        key_limit: Optional[int] = None,
        fail_closed: Optional[bool] = None,
    ) -> tuple[bool, str, int]:
        """Check dual rate limits for API key: individual key limit and tenant aggregate limit.

        Returns:
            (allowed: bool, violation_reason: str, retry_after: int)
        """
        settings = get_api_settings()
        enforce_fail_closed = (
            fail_closed if fail_closed is not None else settings.rate_limit_fail_closed
        )
        actual_key_limit = (
            key_limit if key_limit is not None else settings.api_key_rate_limit_default
        )
        tenant_limit = settings.tenant_api_rate_limit_per_minute

        key_metric = f"rl:apikey:{key_id}"
        tenant_metric = f"rl:tenant_api:{tenant_id}"

        try:
            client = await get_redis_client()
            if client is None:
                if enforce_fail_closed:
                    raise RateLimiterUnavailableError("Rate limiter backend unavailable")
                return True, "", 0

            results = await client.eval(
                _DUAL_RATE_LIMIT_LUA, 2, key_metric, tenant_metric, 60
            )
            k_count, k_ttl, t_count, t_ttl = results[0], results[1], results[2], results[3]

            if k_count > actual_key_limit:
                return (
                    False,
                    f"API key rate limit of {actual_key_limit} requests/min exceeded",
                    max(k_ttl, 1),
                )

            if t_count > tenant_limit:
                return (
                    False,
                    f"Tenant aggregate rate limit of {tenant_limit} requests/min exceeded",
                    max(t_ttl, 1),
                )

            return True, "", 0

        except RateLimiterUnavailableError:
            raise
        except Exception as exc:
            logger.warning("Redis API key rate limiter error: %s", exc)
            if enforce_fail_closed:
                raise RateLimiterUnavailableError("Rate limiter backend error") from exc
            return True, "", 0

    @classmethod
    async def record_api_key_usage(
        cls,
        key_id: str,
        tenant_id: str,
        operation: str = "request",
    ) -> None:
        """Record API key usage telemetry in Redis without blocking PostgreSQL."""
        try:
            client = await get_redis_client()
            if client is None:
                return

            usage_key = f"usage:apikey:{key_id}"
            from datetime import datetime, timezone

            now_iso = datetime.now(timezone.utc).isoformat()
            async with client.pipeline(transaction=False) as pipe:
                pipe.hincrby(usage_key, "total_requests", 1)
                pipe.hset(usage_key, "last_used_at", now_iso)
                pipe.hset(usage_key, "tenant_id", tenant_id)
                pipe.hincrby(usage_key, f"op:{operation}", 1)
                await pipe.execute()
        except Exception as exc:
            logger.debug("Failed to record API key usage in Redis: %s", exc)
