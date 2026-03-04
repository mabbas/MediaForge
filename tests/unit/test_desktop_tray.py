"""Tests for system tray (without actual tray)."""

from __future__ import annotations

import pytest

from desktop.tray import SystemTray


def test_tray_init() -> None:
    """Tray initializes without error."""
    tray = SystemTray()
    assert tray._status_text == "Idle"
    assert tray._is_paused is False


def test_tray_update_status() -> None:
    """Status text updates with active and queued counts."""
    tray = SystemTray()
    tray.update_status(active=2, queued=3)
    assert "2 downloading" in tray._status_text
    assert "3 queued" in tray._status_text


def test_tray_update_status_idle() -> None:
    """Status shows Idle when no activity."""
    tray = SystemTray()
    tray.update_status(active=0, queued=0)
    assert tray._status_text == "Idle"


def test_tray_update_paused() -> None:
    """Paused flag is updated."""
    tray = SystemTray()
    tray.update_status(is_paused=True)
    assert tray._is_paused is True
