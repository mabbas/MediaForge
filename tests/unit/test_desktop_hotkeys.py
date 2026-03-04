"""Tests for hotkey manager."""

from __future__ import annotations

from desktop.hotkeys import HotkeyManager


def test_hotkey_manager_init() -> None:
    mgr = HotkeyManager()
    assert mgr.enabled is True
    assert mgr.list_hotkeys() == []


def test_hotkey_manager_enable_disable() -> None:
    mgr = HotkeyManager()
    mgr.enabled = False
    assert mgr.enabled is False


def test_hotkey_available() -> None:
    mgr = HotkeyManager()
    assert isinstance(mgr.is_available, bool)
