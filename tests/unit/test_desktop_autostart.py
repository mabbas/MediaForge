"""Tests for autostart (platform-dependent)."""

from __future__ import annotations

from desktop.autostart import is_autostart_enabled


def test_autostart_check() -> None:
    """Check doesn't crash regardless of platform."""
    result = is_autostart_enabled()
    assert isinstance(result, bool)
