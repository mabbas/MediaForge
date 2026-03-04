"""Tests for crash handler."""

from __future__ import annotations

from desktop.crash_handler import get_crash_reports, install_crash_handler


def test_get_crash_reports_empty(tmp_path: object) -> None:
    reports = get_crash_reports(str(tmp_path))
    assert reports == []


def test_get_crash_reports(tmp_path: object) -> None:
    from pathlib import Path
    p = Path(str(tmp_path))
    (p / "crash_20240101_120000.log").write_text("test crash report")
    reports = get_crash_reports(str(tmp_path))
    assert len(reports) == 1
    assert "crash_" in reports[0]["name"]


def test_install_crash_handler(tmp_path: object) -> None:
    install_crash_handler(str(tmp_path))
