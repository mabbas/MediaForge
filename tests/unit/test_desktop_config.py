"""Tests for desktop configuration."""

from __future__ import annotations

from desktop.config import DesktopSettings


def test_desktop_settings_defaults() -> None:
    settings = DesktopSettings()
    assert settings.window_title == "GrabItDown"
    assert settings.window_width == 1200
    assert settings.window_height == 800
    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 8765
    assert settings.minimize_to_tray is True


def test_desktop_settings_data_dir() -> None:
    settings = DesktopSettings()
    assert settings.data_dir != ""
    assert "grabitdown" in settings.data_dir.lower() or "grabitdown" in settings.data_dir


def test_desktop_settings_log_dir() -> None:
    settings = DesktopSettings()
    assert settings.log_dir != ""
    assert "log" in settings.log_dir.lower()

