"""Live YouTube transcript extraction (requires network)."""

from __future__ import annotations

import pytest

from src.transcript.youtube_captions import extract_youtube_transcript

YOUTUBE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.mark.integration
def test_extract_youtube_transcript_en_srt() -> None:
    content, fmt = extract_youtube_transcript(
        YOUTUBE_URL, language="en", output_format="srt"
    )
    assert fmt == "srt"
    assert len(content) > 50
    assert "-->" in content


@pytest.mark.integration
def test_extract_youtube_transcript_roman_script() -> None:
    """Roman/auto path must resolve *-en auto caption keys, not plain 'en' only."""
    content, fmt = extract_youtube_transcript(
        YOUTUBE_URL,
        language="en",
        output_format="srt",
        prefer_script="roman",
    )
    assert fmt == "srt"
    assert len(content) > 50
