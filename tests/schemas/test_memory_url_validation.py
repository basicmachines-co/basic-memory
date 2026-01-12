"""Tests for memory URL validation functionality."""

import pytest
from pydantic import ValidationError

from basic_memory.schemas.memory import (
    normalize_memory_url,
    validate_memory_url_path,
    memory_url,
)


class TestValidateMemoryUrlPath:
    """Test the validate_memory_url_path function."""

    def test_valid_paths(self):
        """Test that valid paths pass validation."""
        valid_paths = [
            "notes/meeting",
            "projects/basic-memory",
            "research/findings-2025",
            "specs/search",
            "docs/api-spec",
            "folder/subfolder/note",
            "single-note",
            "notes/with-hyphens",
            "notes/with_underscores",
            "notes/with123numbers",
            "pattern/*",  # Wildcard pattern matching
            "deep/*/pattern",
        ]

        for path in valid_paths:
            assert validate_memory_url_path(path), f"Path '{path}' should be valid"

    def test_invalid_empty_paths(self):
        """Test that empty/whitespace paths fail validation."""
        invalid_paths = [
            "",
            "   ",
            "\t",
            "\n",
            "  \n  ",
        ]

        for path in invalid_paths:
            assert not validate_memory_url_path(path), f"Path '{path}' should be invalid"

    def test_invalid_double_slashes(self):
        """Test that paths with double slashes fail validation."""
        invalid_paths = [
            "notes//meeting",
            "//root",
            "folder//subfolder/note",
            "path//with//multiple//doubles",
            "memory//test",
        ]

        for path in invalid_paths:
            assert not validate_memory_url_path(path), (
                f"Path '{path}' should be invalid (double slashes)"
            )

    def test_invalid_protocol_schemes(self):
        """Test that paths with protocol schemes fail validation."""
        invalid_paths = [
            "http://example.com",
            "https://example.com/path",
            "file://local/path",
            "ftp://server.com",
            "invalid://test",
            "custom://scheme",
        ]

        for path in invalid_paths:
            assert not validate_memory_url_path(path), (
                f"Path '{path}' should be invalid (protocol scheme)"
            )

    def test_invalid_characters(self):
        """Test that paths with invalid characters fail validation."""
        invalid_paths = [
            "notes<with>brackets",
            'notes"with"quotes',
            "notes|with|pipes",
            "notes?with?questions",
        ]

        for path in invalid_paths:
            assert not validate_memory_url_path(path), (
                f"Path '{path}' should be invalid (invalid chars)"
            )


class TestNormalizeMemoryUrl:
    """Test the normalize_memory_url function."""

    def test_valid_normalization(self):
        """Test that valid URLs are properly normalized."""
        test_cases = [
            ("specs/search", "memory://specs/search"),
            ("memory://specs/search", "memory://specs/search"),
            ("notes/meeting-2025", "memory://notes/meeting-2025"),
            ("memory://notes/meeting-2025", "memory://notes/meeting-2025"),
            ("pattern/*", "memory://pattern/*"),
            ("memory://pattern/*", "memory://pattern/*"),
        ]

        for input_url, expected in test_cases:
            result = normalize_memory_url(input_url)
            assert result == expected, (
                f"normalize_memory_url('{input_url}') should return '{expected}', got '{result}'"
            )

    def test_empty_url(self):
        """Test that empty URLs raise ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            normalize_memory_url(None)
        with pytest.raises(ValueError, match="cannot be empty"):
            normalize_memory_url("")

    def test_invalid_double_slashes(self):
        """Test that URLs with double slashes raise ValueError."""
        invalid_urls = [
            "memory//test",
            "notes//meeting",
            "//root",
            "memory://path//with//doubles",
        ]

        for url in invalid_urls:
            with pytest.raises(ValueError, match="contains double slashes"):
                normalize_memory_url(url)

    def test_invalid_protocol_schemes(self):
        """Test that URLs with other protocol schemes raise ValueError."""
        invalid_urls = [
            "http://example.com",
            "https://example.com/path",
            "file://local/path",
            "invalid://test",
        ]

        for url in invalid_urls:
            with pytest.raises(ValueError, match="contains protocol scheme"):
                normalize_memory_url(url)

    def test_whitespace_only(self):
        """Test that whitespace-only URLs raise ValueError."""
        whitespace_urls = [
            "   ",
            "\t",
            "\n",
            "  \n  ",
        ]

        for url in whitespace_urls:
            with pytest.raises(ValueError, match="cannot be empty or whitespace"):
                normalize_memory_url(url)

    def test_invalid_characters(self):
        """Test that URLs with invalid characters raise ValueError."""
        invalid_urls = [
            "notes<brackets>",
            'notes"quotes"',
            "notes|pipes|",
            "notes?questions?",
        ]

        for url in invalid_urls:
            with pytest.raises(ValueError, match="contains invalid characters"):
                normalize_memory_url(url)


class TestMemoryUrlPydanticValidation:
    """Test the MemoryUrl Pydantic type validation."""

    def test_valid_urls_pass_validation(self):
        """Test that valid URLs pass Pydantic validation."""
        valid_urls = [
            "specs/search",
            "memory://specs/search",
            "notes/meeting-2025",
            "projects/basic-memory/docs",
            "pattern/*",
        ]

        for url in valid_urls:
            # Should not raise an exception
            result = memory_url.validate_python(url)
            assert result.startswith("memory://"), (
                f"Validated URL should start with memory://, got {result}"
            )

    def test_invalid_urls_fail_validation(self):
        """Test that invalid URLs fail Pydantic validation with clear errors."""
        invalid_test_cases = [
            ("memory//test", "double slashes"),
            ("invalid://test", "protocol scheme"),
            ("   ", "empty or whitespace"),
            ("notes<brackets>", "invalid characters"),
        ]

        for url, expected_error in invalid_test_cases:
            with pytest.raises(ValidationError) as exc_info:
                memory_url.validate_python(url)

            error_msg = str(exc_info.value)
            assert "value_error" in error_msg, f"Should be a value_error for '{url}'"

    def test_empty_string_fails_validation(self):
        """Test that empty strings fail validation."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            memory_url.validate_python("")

    def test_very_long_urls_fail_maxlength(self):
        """Test that very long URLs fail MaxLen validation."""
        long_url = "a" * 3000  # Exceeds MaxLen(2028)
        with pytest.raises(ValidationError, match="at most 2028"):
            memory_url.validate_python(long_url)

    def test_whitespace_stripped(self):
        """Test that whitespace is properly stripped."""
        urls_with_whitespace = [
            "  specs/search  ",
            "\tprojects/basic-memory\t",
            "\nnotes/meeting\n",
        ]

        for url in urls_with_whitespace:
            result = memory_url.validate_python(url)
            assert not result.startswith(" ") and not result.endswith(" "), (
                f"Whitespace should be stripped from '{url}'"
            )
            assert "memory://" in result, "Result should contain memory:// prefix"


class TestMemoryUrlErrorMessages:
    """Test that error messages are clear and helpful."""

    def test_double_slash_error_message(self):
        """Test specific error message for double slashes."""
        with pytest.raises(ValueError) as exc_info:
            normalize_memory_url("memory//test")

        error_msg = str(exc_info.value)
        assert "memory//test" in error_msg
        assert "double slashes" in error_msg

    def test_protocol_scheme_error_message(self):
        """Test specific error message for protocol schemes."""
        with pytest.raises(ValueError) as exc_info:
            normalize_memory_url("http://example.com")

        error_msg = str(exc_info.value)
        assert "http://example.com" in error_msg
        assert "protocol scheme" in error_msg

    def test_empty_error_message(self):
        """Test specific error message for empty paths."""
        with pytest.raises(ValueError) as exc_info:
            normalize_memory_url("   ")

        error_msg = str(exc_info.value)
        assert "empty or whitespace" in error_msg

    def test_invalid_characters_error_message(self):
        """Test specific error message for invalid characters."""
        with pytest.raises(ValueError) as exc_info:
            normalize_memory_url("notes<brackets>")

        error_msg = str(exc_info.value)
        assert "notes<brackets>" in error_msg
        assert "invalid characters" in error_msg
