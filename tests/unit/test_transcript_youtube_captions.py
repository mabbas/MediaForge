"""Unit tests for YouTube caption extraction helpers (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.exceptions import TranscriptNotAvailableError, TranscriptRateLimitError
from src.transcript.youtube_captions import (
    _find_subtitle_file,
    _raise_if_rate_limited,
    _subtitle_language_candidates,
    is_rate_limit_error,
    normalize_subtitle_content,
    parse_srt_to_text,
    resolve_caption_language,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "captions"


def test_parse_srt_fixture_file() -> None:
    raw = (FIXTURES / "sample.en.srt").read_text(encoding="utf-8")
    text = parse_srt_to_text(raw)
    assert "elephants" in text
    assert "long trunks" in text
    assert "-->" not in text


def test_normalize_subtitle_content_txt_from_srt() -> None:
    raw = (FIXTURES / "sample.en.srt").read_text(encoding="utf-8")
    content, fmt = normalize_subtitle_content(raw, "srt", "txt")
    assert fmt == "txt"
    assert "elephants" in content
    assert "-->" not in content


def test_resolve_caption_language_default_prefers_manual() -> None:
    info = {
        "subtitles": {"en": [{"ext": "srt"}]},
        "automatic_captions": {"ab-en": [{"ext": "vtt"}]},
    }
    code, is_auto = resolve_caption_language(info, "en", None)
    assert code == "en"
    assert is_auto is False


def test_resolve_caption_language_roman_falls_back_to_manual() -> None:
    """When yt-dlp omits automatic_captions, roman may use manual Latin captions."""
    info = {
        "subtitles": {"en": [{"ext": "srt"}]},
        "automatic_captions": {},
    }
    code, is_auto = resolve_caption_language(info, "en", "roman")
    assert code == "en"
    assert is_auto is False


def test_resolve_caption_language_roman_uses_auto_variant() -> None:
    info = {
        "subtitles": {"en": [{"ext": "srt"}]},
        "automatic_captions": {
            "ab-en": [{"ext": "vtt"}],
            "aa-en": [{"ext": "vtt"}],
        },
    }
    code, is_auto = resolve_caption_language(info, "en", "roman")
    assert is_auto is True
    assert code.endswith("-en")


def test_resolve_caption_language_native_manual_only() -> None:
    info = {
        "subtitles": {"en": [{"ext": "srt"}]},
        "automatic_captions": {"ab-en": [{"ext": "vtt"}]},
    }
    code, is_auto = resolve_caption_language(info, "en", "native")
    assert code == "en"
    assert is_auto is False


def test_resolve_caption_language_no_tracks() -> None:
    info = {"subtitles": {}, "automatic_captions": {}}
    with pytest.raises(TranscriptNotAvailableError, match="No transcript available"):
        resolve_caption_language(info, "hi", None)


def test_resolve_caption_language_native_missing() -> None:
    info = {
        "subtitles": {},
        "automatic_captions": {"ab-en": [{"ext": "vtt"}]},
    }
    with pytest.raises(TranscriptNotAvailableError, match="manually uploaded"):
        resolve_caption_language(info, "en", "native")


def test_subtitle_language_candidates_includes_exact_and_variants() -> None:
    info = {
        "subtitles": {"en": [{"ext": "srt"}]},
        "automatic_captions": {"ab-en": [{"ext": "vtt"}]},
    }
    codes = _subtitle_language_candidates(info, "en", None)
    assert "en" in codes
    assert "ab-en" in codes


def test_find_subtitle_file_nested_name(tmp_path: Path) -> None:
    sub = tmp_path / "abc123.en.srt"
    sub.write_text("1\n00:00:01 --> 00:00:02\nhi", encoding="utf-8")
    found = _find_subtitle_file(tmp_path, "abc123")
    assert found == sub


def test_is_rate_limit_error() -> None:
    assert is_rate_limit_error("HTTP Error 429: Too Many Requests")
    assert not is_rate_limit_error("Subtitle file was not written")


def test_raise_if_rate_limited() -> None:
    with pytest.raises(TranscriptRateLimitError):
        _raise_if_rate_limited("Unable to download video subtitles: HTTP Error 429")
