"""Roman (Latin) transliteration for Hindi/Urdu subtitle content."""

from __future__ import annotations

import re

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")


def language_base(language: str) -> str:
    """Return the primary language code (e.g. hi-IN -> hi)."""
    return (language or "").split("-")[0].lower()


def is_hindi_language(language: str) -> bool:
    return language_base(language) == "hi"


def is_urdu_language(language: str) -> bool:
    return language_base(language) == "ur"


def is_indic_roman_language(language: str) -> bool:
    """Languages where users may request Roman/Latin script output."""
    return is_hindi_language(language) or is_urdu_language(language)


def content_needs_romanization(content: str, language: str) -> bool:
    """True when *content* still uses native script for *language*."""
    if is_hindi_language(language):
        return bool(_DEVANAGARI_RE.search(content))
    if is_urdu_language(language):
        return bool(_ARABIC_RE.search(content))
    return False


def romanize_text(text: str, language: str) -> str:
    """Transliterate a single line of subtitle text to Roman/Latin."""
    if not text or not content_needs_romanization(text, language):
        return text

    if is_hindi_language(language):
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate

        return transliterate(text, sanscript.DEVANAGARI, sanscript.OPTITRANS)

    if is_urdu_language(language):
        from unidecode import unidecode

        return unidecode(text)

    return text


def _is_srt_timing_line(line: str) -> bool:
    return "-->" in line


def romanize_subtitle_content(content: str, language: str, output_format: str) -> str:
    """Romanize subtitle body while preserving SRT/VTT structure."""
    if not is_indic_roman_language(language):
        return content
    if not content_needs_romanization(content, language):
        return content

    fmt = output_format.lower()
    if fmt in ("srt", "vtt"):
        out_lines: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if (
                not stripped
                or stripped.isdigit()
                or _is_srt_timing_line(stripped)
                or stripped.startswith("WEBVTT")
                or stripped.startswith("NOTE")
            ):
                out_lines.append(line)
            else:
                out_lines.append(romanize_text(line, language))
        return "\n".join(out_lines)

    if fmt == "txt":
        return "\n".join(romanize_text(line, language) for line in content.splitlines())

    return romanize_text(content, language)
