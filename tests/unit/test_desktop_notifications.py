"""Tests for notification manager."""

from __future__ import annotations

from desktop.notifications import NotificationManager


def test_notification_manager_init() -> None:
    mgr = NotificationManager()
    assert mgr.enabled is True
    assert len(mgr.get_history()) == 0


def test_notification_send() -> None:
    mgr = NotificationManager()
    mgr.notify("Test", "Hello", "info")
    assert len(mgr.get_history()) == 1
    assert mgr.get_history()[0]["title"] == "Test"


def test_notification_disabled() -> None:
    mgr = NotificationManager()
    mgr.enabled = False
    mgr.notify("Test", "Hello")
    assert len(mgr.get_history()) == 0


def test_notification_helpers() -> None:
    mgr = NotificationManager()
    mgr.download_completed("Video", "/path")
    mgr.download_failed("Video", "Error")
    mgr.download_started("Video", "https://test.com")
    mgr.clipboard_url_detected("https://youtube.com")
    assert len(mgr.get_history()) == 4


def test_notification_history_limit() -> None:
    mgr = NotificationManager()
    for i in range(60):
        mgr.notify(f"Test {i}", "msg")
    assert len(mgr.get_history()) == 50


def test_notification_clear_history() -> None:
    mgr = NotificationManager()
    mgr.notify("Test", "msg")
    mgr.clear_history()
    assert len(mgr.get_history()) == 0
