"""Tests for application configuration loading."""

from pathlib import Path

import pytest

from src.config import get_settings, resolve_download_output_directory, reset_settings_cache


def test_load_default_config() -> None:
    """Verify default.yaml loads successfully."""
    settings = get_settings()
    assert settings.app.name == "GrabItDown"
    assert settings.app.version == "0.1.0"


def test_download_settings_defaults() -> None:
    """Verify download section has correct defaults."""
    settings = get_settings()
    assert settings.download.max_concurrent_downloads == 3
    assert settings.download.absolute_max_concurrent == 5
    assert settings.download.queue_max_size == 100
    assert settings.download.retry_max_attempts == 3
    assert settings.download.max_file_size_mb == 5000


def test_video_settings_defaults() -> None:
    """Verify video section defaults."""
    settings = get_settings()
    assert settings.video.default_quality == "1080p"
    assert settings.video.preferred_format == "mp4"
    assert settings.video.embed_subtitles is True


def test_audio_settings_defaults() -> None:
    """Verify audio section defaults."""
    settings = get_settings()
    assert settings.audio.default_format == "mp3"
    assert settings.audio.default_bitrate == "192k"


def test_transcript_settings_defaults() -> None:
    """Verify transcript section defaults."""
    settings = get_settings()
    assert "en" in settings.transcript.default_languages
    assert "ur" in settings.transcript.default_languages
    assert settings.transcript.whisper_model == "medium"


def test_playlist_settings_defaults() -> None:
    """Verify playlist section defaults."""
    settings = get_settings()
    assert settings.playlist.max_playlist_size == 500
    assert settings.playlist.skip_existing is True


def test_resume_settings_defaults() -> None:
    """Verify resume section defaults."""
    settings = get_settings()
    assert settings.resume.enabled is True
    assert settings.resume.max_auto_retries == 5


def test_provider_settings_defaults() -> None:
    """Verify provider enable/disable defaults."""
    settings = get_settings()
    assert settings.providers.youtube.enabled is True
    assert settings.providers.facebook.enabled is False
    assert settings.providers.generic.enabled is True


def test_download_output_directory_default_is_user_downloads() -> None:
    """default.yaml uses ~/Downloads."""
    settings = get_settings()
    expected = str(Path.home() / "Downloads")
    assert settings.download.output_directory == expected


def test_resolve_download_output_directory_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GID_DOWNLOAD__OUTPUT_DIRECTORY in env wins over ~/Downloads."""
    custom = tmp_path / "my-videos"
    monkeypatch.setenv("GID_DOWNLOAD__OUTPUT_DIRECTORY", str(custom))
    reset_settings_cache()
    resolved = resolve_download_output_directory()
    assert resolved == custom.resolve()
    assert custom.is_dir()


def test_resolve_download_output_directory_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without env override, downloads go to ~/Downloads."""
    monkeypatch.delenv("GID_DOWNLOAD__OUTPUT_DIRECTORY", raising=False)
    reset_settings_cache()
    resolved = resolve_download_output_directory()
    assert resolved == (Path.home() / "Downloads").resolve()


def test_config_singleton() -> None:
    """get_settings() returns same instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2

