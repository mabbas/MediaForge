"""Tests for GrabItDown integrity checker."""

from src.resume.integrity_checker import IntegrityChecker


def test_check_valid_file(tmp_path):
    """File matching expected size is valid."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    part.write_bytes(b"x" * 1000)

    valid, reason, position = checker.check_part_file(part, expected_bytes=1000)
    assert valid is True
    assert position == 1000


def test_check_empty_file(tmp_path):
    """Empty file is valid, resume from 0."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    part.touch()

    valid, reason, position = checker.check_part_file(part, expected_bytes=0)
    assert valid is True
    assert position == 0


def test_check_smaller_file(tmp_path):
    """Smaller file is valid, resume from actual size."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    part.write_bytes(b"x" * 500)

    valid, reason, position = checker.check_part_file(part, expected_bytes=1000)
    assert valid is True
    assert position == 500


def test_check_larger_file(tmp_path):
    """Larger file needs truncation."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    part.write_bytes(b"x" * 2000)

    valid, reason, position = checker.check_part_file(part, expected_bytes=1000)
    assert valid is False
    assert position == 1000
    assert "truncation" in reason.lower()


def test_check_missing_file(tmp_path):
    """Missing file is invalid."""
    checker = IntegrityChecker()
    part = tmp_path / "nonexistent.part"

    valid, reason, position = checker.check_part_file(part, expected_bytes=1000)
    assert valid is False
    assert position == 0


def test_hash_last_chunk(tmp_path):
    """Hash of last chunk is consistent."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    data = b"Hello World " * 100000
    part.write_bytes(data)

    hash1 = checker.hash_last_chunk(part)
    hash2 = checker.hash_last_chunk(part)

    assert hash1 is not None
    assert hash1 == hash2


def test_hash_empty_file(tmp_path):
    """Hash of empty file returns None."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    part.touch()

    assert checker.hash_last_chunk(part) is None


def test_verify_hash_match(tmp_path):
    """Matching hash returns True."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    part.write_bytes(b"test data " * 10000)

    expected = checker.hash_last_chunk(part)
    assert expected is not None
    assert checker.verify_hash(part, expected) is True


def test_verify_hash_mismatch(tmp_path):
    """Wrong hash returns False."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    part.write_bytes(b"test data " * 10000)

    assert checker.verify_hash(part, "wrong_hash") is False


def test_truncate_file(tmp_path):
    """Truncates file to specified position."""
    checker = IntegrityChecker()
    part = tmp_path / "test.part"
    part.write_bytes(b"x" * 2000)

    result = checker.truncate_to_position(part, 1000)
    assert result is True
    assert part.stat().st_size == 1000

