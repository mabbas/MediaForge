"""Tests for GrabItDown retry handler."""

import threading

from src.resume.retry_handler import RetryConfig, RetryHandler, RetryResult


def test_success_first_attempt():
    """Operation succeeds on first try."""
    handler = RetryHandler(RetryConfig(max_attempts=3))
    result = handler.execute(lambda: "ok")
    assert result.success is True
    assert result.result == "ok"
    assert result.attempts == 1
    assert result.total_wait_time == 0.0


def test_success_after_retries():
    """Operation succeeds after failures."""
    call_count = [0]

    def flaky():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ConnectionError("fail")
        return "ok"

    handler = RetryHandler(RetryConfig(max_attempts=5, backoff_base=0.01, jitter=False))
    result = handler.execute(flaky)
    assert result.success is True
    assert result.attempts == 3


def test_all_attempts_exhausted():
    """All retries fail."""
    handler = RetryHandler(RetryConfig(max_attempts=3, backoff_base=0.01, jitter=False))
    result = handler.execute(lambda: (_ for _ in ()).throw(ConnectionError("always fail")))
    assert result.success is False
    assert result.attempts == 3
    assert result.last_error is not None


def test_on_retry_callback():
    """on_retry called before each retry."""
    retry_log = []

    def on_retry(attempt, error, wait):
        retry_log.append(attempt)

    handler = RetryHandler(RetryConfig(max_attempts=3, backoff_base=0.01, jitter=False))
    handler.execute(lambda: (_ for _ in ()).throw(Exception("fail")), on_retry=on_retry)

    assert len(retry_log) == 2


def test_cancel_during_wait():
    """Cancel event stops retry during wait."""
    cancel = threading.Event()

    def slow_fail():
        raise ConnectionError("fail")

    handler = RetryHandler(RetryConfig(max_attempts=5, backoff_base=10.0, jitter=False))

    threading.Timer(0.2, cancel.set).start()

    result = handler.execute(slow_fail, cancel_event=cancel)
    assert result.success is False
    assert result.attempts < 5


def test_backoff_calculation():
    """Backoff increases exponentially."""
    handler = RetryHandler(RetryConfig(backoff_base=2.0, backoff_max=60.0))
    assert handler.calculate_backoff(1) == 1.0
    assert handler.calculate_backoff(2) == 2.0
    assert handler.calculate_backoff(3) == 4.0
    assert handler.calculate_backoff(4) == 8.0


def test_backoff_max_cap():
    """Backoff capped at maximum."""
    handler = RetryHandler(RetryConfig(backoff_base=2.0, backoff_max=10.0))
    assert handler.calculate_backoff(10) == 10.0


def test_non_retryable_exception():
    """Non-matching exceptions are not retried."""
    handler = RetryHandler(
        RetryConfig(
            max_attempts=3,
            retry_on_exceptions=(ConnectionError,),
        )
    )

    result = None
    try:
        result = handler.execute(lambda: (_ for _ in ()).throw(ValueError("not retryable")))
    except ValueError:
        pass

    assert result is None


def test_retry_result_properties():
    """RetryResult has all expected properties."""
    result = RetryResult(success=True, result="data", attempts=2, total_wait_time=3.5)
    assert result.success is True
    assert result.result == "data"
    assert result.attempts == 2
    assert result.last_error is None
    assert result.total_wait_time == 3.5

