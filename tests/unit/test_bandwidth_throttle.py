"""Tests for GrabItDown bandwidth throttle."""

import time

from src.download.bandwidth_throttle import BandwidthThrottle, GlobalBandwidthThrottle


def test_unlimited_no_block():
    """Unlimited throttle returns immediately."""
    throttle = BandwidthThrottle(limit_bytes_per_second=0)
    start = time.monotonic()
    for _ in range(100):
        throttle.acquire(1048576)  # 1MB
    elapsed = time.monotonic() - start
    assert elapsed < 0.1  # should be instant


def test_throttle_slows_down():
    """Throttle adds delay for rate limiting."""
    throttle = BandwidthThrottle(limit_bytes_per_second=1048576)  # 1 MB/s
    start = time.monotonic()
    throttle.acquire(2097152)  # 2MB
    elapsed = time.monotonic() - start
    assert elapsed >= 0.5  # should take ~1-2 seconds


def test_set_limit_runtime():
    """Can change limit at runtime."""
    throttle = BandwidthThrottle(limit_bytes_per_second=1048576)
    assert throttle.limit == 1048576
    throttle.set_limit(5242880)
    assert throttle.limit == 5242880


def test_set_unlimited():
    """Setting 0 makes it unlimited."""
    throttle = BandwidthThrottle(limit_bytes_per_second=1048576)
    throttle.set_limit(0)
    start = time.monotonic()
    throttle.acquire(104857600)  # 100MB
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_global_singleton():
    """Global throttle is singleton."""
    GlobalBandwidthThrottle.reset()
    t1 = GlobalBandwidthThrottle.get_instance(1000)
    t2 = GlobalBandwidthThrottle.get_instance(2000)
    assert t1 is t2
    assert t1.limit == 1000  # first init wins
    GlobalBandwidthThrottle.reset()
