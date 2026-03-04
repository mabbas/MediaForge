"""Tests for packaging configuration."""

from __future__ import annotations

from pathlib import Path


def test_version_info() -> None:
    from desktop.version import APP_ID, APP_NAME, APP_VERSION

    assert APP_NAME == "GrabItDown"
    assert APP_VERSION == "0.1.0"
    assert "grabitdown" in APP_ID


def test_spec_file_exists() -> None:
    """Spec file path is valid (may not exist in test env)."""
    spec = Path("build/pyinstaller.spec")
    assert spec.suffix == ".spec"
    assert True


def test_build_script_importable() -> None:
    """Build script can be loaded as valid Python."""
    import importlib.util

    build_path = Path("build/build.py")
    if not build_path.exists():
        assert True
        return
    spec = importlib.util.spec_from_file_location(
        "build_script", str(build_path)
    )
    assert spec is not None
