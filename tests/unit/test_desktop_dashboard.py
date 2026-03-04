"""Tests for dashboard HTML generation."""

from __future__ import annotations

from desktop.dashboard import get_dashboard_html


def test_dashboard_html_generated() -> None:
    html = get_dashboard_html("http://localhost:8765")
    assert len(html) > 5000
    assert "GrabItDown" in html
    assert "localhost:8765" in html
    assert "<script>" in html


def test_dashboard_has_download_form() -> None:
    html = get_dashboard_html("http://localhost:8765")
    assert "url-input" in html
    assert "submitDownload" in html


def test_dashboard_has_websocket() -> None:
    html = get_dashboard_html("http://localhost:8765")
    assert "ws://" in html
    assert "ws/progress" in html


def test_dashboard_has_stats() -> None:
    html = get_dashboard_html("http://localhost:8765")
    assert "stat-active" in html
    assert "stat-queued" in html
    assert "stat-completed" in html


def test_dashboard_has_options() -> None:
    html = get_dashboard_html("http://localhost:8765")
    assert "mode-select" in html
    assert "quality-select" in html
    assert "priority-select" in html

