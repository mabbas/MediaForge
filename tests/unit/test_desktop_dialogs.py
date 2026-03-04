"""Tests for native dialogs (without actual GUI)."""

from __future__ import annotations

from unittest.mock import MagicMock

from desktop.dialogs import NativeDialogs


def test_dialogs_init() -> None:
    """Dialogs initialize with no window."""
    dialogs = NativeDialogs()
    assert dialogs._window is None


def test_dialogs_init_with_window() -> None:
    """Dialogs initialize with mock window."""
    mock_window = MagicMock()
    dialogs = NativeDialogs(window=mock_window)
    assert dialogs._window is mock_window
