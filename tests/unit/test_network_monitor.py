"""Tests for GrabItDown network monitor."""

import time

from src.resume.network_monitor import NetworkMonitor


def test_initial_state():
    """Monitor starts as connected."""
    monitor = NetworkMonitor(check_interval=1.0)
    assert monitor.state == "connected"
    assert monitor.is_connected is True


def test_check_now():
    """check_now performs immediate check."""
    monitor = NetworkMonitor(check_hosts=[("www.google.com", 443)], timeout=3.0)
    result = monitor.check_now()
    assert isinstance(result, bool)


def test_callback_registration():
    """Callbacks can be registered."""
    monitor = NetworkMonitor()
    connected_calls = []
    disconnected_calls = []

    monitor.on_connected(lambda: connected_calls.append(1))
    monitor.on_disconnected(lambda: disconnected_calls.append(1))

    monitor._fire_callbacks(monitor._on_connected)
    assert len(connected_calls) == 1

    monitor._fire_callbacks(monitor._on_disconnected)
    assert len(disconnected_calls) == 1


def test_callback_error_handling():
    """Failing callbacks don't crash the monitor."""
    monitor = NetworkMonitor()
    monitor.on_connected(lambda: 1 / 0)
    monitor.on_connected(lambda: None)

    monitor._fire_callbacks(monitor._on_connected)


def test_start_and_stop():
    """Monitor starts and stops cleanly."""
    monitor = NetworkMonitor(check_interval=0.2)
    monitor.start()
    assert monitor._running is True
    time.sleep(0.3)
    monitor.stop()
    assert monitor._running is False


def test_check_unreachable_host():
    """Unreachable host returns False."""
    monitor = NetworkMonitor(check_hosts=[("192.0.2.1", 12345)], timeout=0.5)
    result = monitor._check_connectivity()
    assert result is False

