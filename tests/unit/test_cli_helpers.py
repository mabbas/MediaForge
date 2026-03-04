"""Tests for GrabItDown CLI helper functions."""

from src.cli.cli import _parse_items, _read_url_file


def test_parse_items_all():
    """'all' returns 'all'."""
    assert _parse_items("all") == "all"
    assert _parse_items("ALL") == "all"


def test_parse_items_single():
    """Single numbers parsed."""
    result = _parse_items("1,3,5")
    assert result == [1, 3, 5]


def test_parse_items_range():
    """Ranges expanded."""
    result = _parse_items("1-3")
    assert result == [1, 2, 3]


def test_parse_items_mixed():
    """Mixed singles and ranges."""
    result = _parse_items("1,3,5-8")
    assert result == [1, 3, 5, 6, 7, 8]


def test_read_url_file(tmp_path):
    """URLs read from file."""
    f = tmp_path / "urls.txt"
    f.write_text(
        "https://youtube.com/watch?v=1\n"
        "# comment\n"
        "\n"
        "https://youtube.com/watch?v=2\n"
    )
    result = _read_url_file(str(f))
    assert len(result) == 2
    assert result[0] == "https://youtube.com/watch?v=1"


def test_read_url_file_empty(tmp_path):
    """Empty file returns empty list."""
    f = tmp_path / "empty.txt"
    f.write_text("# only comments\n\n")
    result = _read_url_file(str(f))
    assert result == []
