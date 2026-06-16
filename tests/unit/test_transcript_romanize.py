"""Tests for Hindi/Urdu Roman transliteration."""

from __future__ import annotations

from src.transcript.romanize import (
    content_needs_romanization,
    is_indic_roman_language,
    romanize_subtitle_content,
    romanize_text,
)


def test_is_indic_roman_language() -> None:
    assert is_indic_roman_language("hi")
    assert is_indic_roman_language("hi-IN")
    assert is_indic_roman_language("ur-PK")
    assert not is_indic_roman_language("en")


def test_romanize_hindi_devanagari() -> None:
    text = "मैं आपको बताता हूँ"
    result = romanize_text(text, "hi")
    assert content_needs_romanization(text, "hi")
    assert not content_needs_romanization(result, "hi")
    assert all(ord(c) < 128 or c in "\n\r\t" for c in result)


def test_romanize_srt_preserves_timing() -> None:
    srt = (
        "1\n"
        "00:00:01,200 --> 00:00:03,360\n"
        "कुछ साल पहले\n"
    )
    out = romanize_subtitle_content(srt, "hi", "srt")
    assert "00:00:01,200 --> 00:00:03,360" in out
    assert "1\n" in out or out.startswith("1")
    assert "कुछ" not in out


def test_romanize_skips_already_latin() -> None:
    text = "main aapko batata hun"
    assert not content_needs_romanization(text, "hi")
    assert romanize_text(text, "hi") == text
