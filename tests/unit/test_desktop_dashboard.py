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


def test_dashboard_has_tabs() -> None:
    html = get_dashboard_html("http://localhost:8765")
    assert "switchTab" in html
    assert "panel-download" in html
    assert "panel-extract" in html
    assert "panel-merge" in html
    assert "tab-btn-download" in html
    assert "Extract Clip" in html
    assert "Merge Clips" in html


def test_dashboard_has_clip_section() -> None:
    html = get_dashboard_html("http://localhost:8765")
    assert "Clip Extraction" in html
    assert "clip-source" in html
    assert "clip-start" in html
    assert "clip-end" in html
    assert "00:10:00" in html
    assert "00:30:00" in html
    assert "extractClip" in html
    assert "browseClipSource" in html
    assert "clip-job-card" in html
    assert "clip-progress-text" in html
    assert "Browse" in html


def test_dashboard_has_merge_browse() -> None:
    html = get_dashboard_html("http://localhost:8765")
    assert "merge-clips-list" in html
    assert "browseMergeClip" in html
    assert "addMergeClipInput" in html
    assert "mergeClips" in html

