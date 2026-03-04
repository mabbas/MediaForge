"""In-memory rate limiting middleware (per-minute)."""

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter by client IP."""

    def __init__(self, app, requests_per_minute: int = 60, burst: int = 10):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.burst = burst
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _client_key(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _is_localhost(self, request: Request) -> bool:
        key = self._client_key(request)
        return key in ("127.0.0.1", "::1", "localhost")

    async def dispatch(self, request: Request, call_next):
        if self._is_localhost(request):
            return await call_next(request)

        key = self._client_key(request)
        now = time.time()
        window_start = now - 60

        self._requests[key] = [t for t in self._requests[key] if t > window_start]

        if len(self._requests[key]) >= self.requests_per_minute:
            return JSONResponse(
                status_code=429,
                content={"success": False, "error": "Rate limit exceeded"},
            )

        self._requests[key].append(now)
        return await call_next(request)
