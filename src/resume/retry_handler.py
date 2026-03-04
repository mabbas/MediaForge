"""GrabItDown retry handler — manages automatic retries with exponential backoff."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
        retry_on_exceptions: Tuple[type[BaseException], ...] = (Exception,),
        jitter: bool = True,
    ) -> None:
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.retry_on_exceptions = retry_on_exceptions
        self.jitter = jitter


class RetryResult:
    """Result of a retry operation."""

    def __init__(
        self,
        success: bool,
        result: Any = None,
        attempts: int = 0,
        last_error: Optional[Exception] = None,
        total_wait_time: float = 0.0,
    ) -> None:
        self.success = success
        self.result = result
        self.attempts = attempts
        self.last_error = last_error
        self.total_wait_time = total_wait_time


class RetryHandler:
    """Handles automatic retries with exponential backoff."""

    def __init__(self, config: Optional[RetryConfig] = None) -> None:
        self._config = config or RetryConfig()

    def execute(
        self,
        operation: Callable[[], Any],
        on_retry: Optional[Callable[[int, Exception, float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> RetryResult:
        """Execute operation with retry logic."""
        total_wait = 0.0
        last_error: Optional[Exception] = None

        for attempt in range(1, self._config.max_attempts + 1):
            try:
                result = operation()
                return RetryResult(
                    success=True,
                    result=result,
                    attempts=attempt,
                    total_wait_time=total_wait,
                )
            except self._config.retry_on_exceptions as exc:
                last_error = exc

                if attempt >= self._config.max_attempts:
                    logger.warning(
                        "All %d retry attempts exhausted. Last error: %s",
                        attempt,
                        exc,
                    )
                    break

                wait = self.calculate_backoff(attempt)

                if self._config.jitter:
                    import random

                    wait *= 0.5 + random.random()

                total_wait += wait

                logger.info(
                    "Retry %d/%d: waiting %.1fs (error: %s)",
                    attempt,
                    self._config.max_attempts,
                    wait,
                    exc,
                )

                if on_retry:
                    try:
                        on_retry(attempt, exc, wait)
                    except Exception:
                        # Swallow callback errors
                        pass

                if cancel_event:
                    if cancel_event.wait(timeout=wait):
                        logger.info("Retry cancelled")
                        return RetryResult(
                            success=False,
                            attempts=attempt,
                            last_error=last_error,
                            total_wait_time=total_wait,
                        )
                else:
                    time.sleep(wait)

        return RetryResult(
            success=False,
            attempts=self._config.max_attempts,
            last_error=last_error,
            total_wait_time=total_wait,
        )

    def calculate_backoff(self, attempt: int) -> float:
        """Calculate wait time for a given attempt."""
        wait = min(self._config.backoff_base ** (attempt - 1), self._config.backoff_max)
        return wait

