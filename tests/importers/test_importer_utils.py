"""Tests for importer utility functions."""

from datetime import datetime

from basic_memory.importers.utils import clean_filename, format_timestamp


def test_clean_filename():
    """Test clean_filename utility function."""
    # Test with normal string
    assert clean_filename("Hello World") == "Hello_World"

    # Test with punctuation
    assert clean_filename("Hello, World!") == "Hello_World"

    # Test with special characters
    assert clean_filename("File[1]/with\\special:chars") == "File_1_with_special_chars"

    # Test with long string (over 100 chars)
    long_str = "a" * 120
    assert len(clean_filename(long_str)) == 100

    # Test with empty string
    assert clean_filename("") == "untitled"

    # Test with only special characters
    # Some implementations may return empty string or underscore
    result = clean_filename("!@#$%^&*()")
    assert result in ["untitled", "_", ""]


def test_format_timestamp():
    """Test format_timestamp utility function."""
    # Test with datetime object
    dt = datetime(2023, 1, 1, 12, 30, 45)
    assert format_timestamp(dt) == "2023-01-01 12:30:45"

    # Test with ISO format string
    iso_str = "2023-01-01T12:30:45Z"
    assert format_timestamp(iso_str) == "2023-01-01 12:30:45"

    # Test with Unix timestamp as int
    unix_ts = 1672577445  # 2023-01-01 12:30:45 UTC
    formatted = format_timestamp(unix_ts)
    # The exact format may vary by timezone, so we just check for the year
    assert "2023" in formatted

    # Test with Unix timestamp as string
    unix_str = "1672577445"
    formatted = format_timestamp(unix_str)
    assert "2023" in formatted

    # Test with unparseable string
    assert format_timestamp("not a timestamp") == "not a timestamp"

    # Test with non-timestamp object
    assert format_timestamp(None) == "None"
