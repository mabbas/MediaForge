"""Tests for GrabItDown usage tracker."""

from __future__ import annotations

from datetime import date

import pytest

from src.exceptions import LimitExceededError
from src.features.feature_flags import load_feature_flags
from src.features.feature_gate import FeatureGate
from src.features.usage_tracker import UsageTracker


def _make_tracker() -> UsageTracker:
    flags = load_feature_flags()
    gate = FeatureGate(flags)
    return UsageTracker(gate)


def test_increment_and_get():
    """Basic increment and retrieval."""
    tracker = _make_tracker()
    tracker.increment("alice", "daily_downloads")
    assert tracker.get_usage("alice", "daily_downloads") == 1


def test_increment_multiple():
    """Multiple increments accumulate."""
    tracker = _make_tracker()
    for _ in range(5):
        tracker.increment("alice", "daily_downloads")
    assert tracker.get_usage("alice", "daily_downloads") == 5


def test_limit_exceeded_basic_tier():
    """Exceeding basic tier limit raises error."""
    tracker = _make_tracker()

    # Basic tier daily_limit is 5 in default config.
    for _ in range(5):
        tracker.increment("alice", "daily_downloads", tier="basic")

    with pytest.raises(LimitExceededError):
        tracker.increment("alice", "daily_downloads", tier="basic")


def test_unlimited_platinum():
    """Platinum tier has unlimited downloads."""
    tracker = _make_tracker()

    # Platinum tier daily_limit is -1 (unlimited).
    for _ in range(100):
        tracker.increment("alice", "daily_downloads", tier="platinum")

    # Should not raise
    assert tracker.get_usage("alice", "daily_downloads") == 100


def test_get_remaining():
    """Remaining quota calculated correctly."""
    tracker = _make_tracker()
    tracker.increment("alice", "daily_downloads", 3, tier="basic")
    remaining = tracker.get_remaining("alice", "daily_downloads", tier="basic")
    assert remaining == 2  # 5 - 3


def test_get_remaining_unlimited():
    """Unlimited returns -1."""
    tracker = _make_tracker()
    remaining = tracker.get_remaining("alice", "daily_downloads", tier="platinum")
    assert remaining == -1


def test_separate_users():
    """Users have separate counters."""
    tracker = _make_tracker()
    tracker.increment("alice", "daily_downloads")
    tracker.increment("bob", "daily_downloads")
    assert tracker.get_usage("alice", "daily_downloads") == 1
    assert tracker.get_usage("bob", "daily_downloads") == 1


def test_reset_user():
    """Reset clears a user's usage."""
    tracker = _make_tracker()
    tracker.increment("alice", "daily_downloads", 5)
    tracker.reset("alice")
    assert tracker.get_usage("alice", "daily_downloads") == 0


def test_get_all_usage():
    """Get all counters for a user."""
    tracker = _make_tracker()
    tracker.increment("alice", "daily_downloads", 3)
    tracker.increment("alice", "batch_size", 2)
    all_usage = tracker.get_all_usage("alice")
    assert all_usage["daily_downloads"] == 3
    assert all_usage["batch_size"] == 2

