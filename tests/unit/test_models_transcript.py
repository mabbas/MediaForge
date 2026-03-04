"""Tests for GrabItDown transcript models."""

import pytest
from pydantic import ValidationError

from src.models.enums import TranscriptFormat, TranscriptSource
from src.models.transcript import TranscriptRequest, TranscriptResult, TranscriptSegment


def test_transcript_segment_start_timestamp() -> None:
    """83.456 seconds → '00:01:23.456'."""
    seg = TranscriptSegment(start_seconds=83.456, end_seconds=87.0, text="Hello")
    assert seg.start_timestamp == "00:01:23.456"


def test_transcript_segment_end_timestamp() -> None:
    """87.123 seconds → '00:01:27.123'."""
    seg = TranscriptSegment(start_seconds=0, end_seconds=87.123, text="Hello")
    assert seg.end_timestamp == "00:01:27.123"


def test_transcript_segment_zero_start() -> None:
    """0.0 seconds → '00:00:00.000'."""
    seg = TranscriptSegment(start_seconds=0.0, end_seconds=5.0, text="Hello")
    assert seg.start_timestamp == "00:00:00.000"


def test_transcript_segment_large_timestamp() -> None:
    """3661.5 seconds → '01:01:01.500'."""
    seg = TranscriptSegment(start_seconds=3661.5, end_seconds=3665.0, text="Hello")
    assert seg.start_timestamp == "01:01:01.500"


def test_transcript_segment_duration() -> None:
    """Duration calculated correctly."""
    seg = TranscriptSegment(start_seconds=83.456, end_seconds=87.123, text="Hello")
    assert abs(seg.duration_seconds - 3.667) < 0.001


def test_transcript_request_defaults() -> None:
    """Verify default values."""
    req = TranscriptRequest(url="https://youtube.com/watch?v=test")
    assert req.language == "en"
    assert req.output_format == TranscriptFormat.SRT
    assert req.use_whisper is False
    assert req.whisper_model == "medium"


def test_transcript_request_language_valid() -> None:
    """2-character codes pass."""
    req = TranscriptRequest(url="https://youtube.com/watch?v=test", language="ur")
    assert req.language == "ur"


def test_transcript_request_language_wildcard() -> None:
    """Wildcard '*' passes."""
    req = TranscriptRequest(url="https://youtube.com/watch?v=test", language="*")
    assert req.language == "*"


def test_transcript_request_language_invalid_long() -> None:
    """'english' fails (too long)."""
    with pytest.raises(ValidationError):
        TranscriptRequest(url="https://youtube.com/watch?v=test", language="english")


def test_transcript_request_language_invalid_upper() -> None:
    """'EN' fails (must be lowercase)."""
    with pytest.raises(ValidationError):
        TranscriptRequest(url="https://youtube.com/watch?v=test", language="EN")


def test_transcript_request_url_validation() -> None:
    """Invalid URL fails."""
    with pytest.raises(ValidationError):
        TranscriptRequest(url="not-a-url")


def test_transcript_result_segment_count() -> None:
    """segment_count returns len(segments)."""
    result = TranscriptResult(
        url="https://test.com",
        language="en",
        source=TranscriptSource.YOUTUBE_CC,
        output_format=TranscriptFormat.SRT,
        segments=[
            TranscriptSegment(start_seconds=0, end_seconds=5, text="Hello"),
            TranscriptSegment(start_seconds=5, end_seconds=10, text="World"),
        ],
    )
    assert result.segment_count == 2


def test_transcript_result_empty_segments() -> None:
    """Empty segments → count 0."""
    result = TranscriptResult(
        url="https://test.com",
        language="en",
        source=TranscriptSource.WHISPER,
        output_format=TranscriptFormat.TXT,
    )
    assert result.segment_count == 0


def test_transcript_result_auto_created_at() -> None:
    """created_at auto-populated."""
    result = TranscriptResult(
        url="https://test.com",
        language="en",
        source=TranscriptSource.YOUTUBE_CC,
        output_format=TranscriptFormat.SRT,
    )
    assert result.created_at is not None

