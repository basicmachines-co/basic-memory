"""Tests for parse_tags utility function."""

from typing import List, Union

import pytest

from basic_memory.utils import parse_tags


@pytest.mark.parametrize(
    "input_tags,expected",
    [
        # None input
        (None, []),
        # List inputs
        ([], []),
        (["tag1", "tag2"], ["tag1", "tag2"]),
        (["tag1", "", "tag2"], ["tag1", "tag2"]),  # Empty tags are filtered
        ([" tag1 ", " tag2 "], ["tag1", "tag2"]),  # Whitespace is stripped
        # String inputs
        ("", []),
        ("tag1", ["tag1"]),
        ("tag1,tag2", ["tag1", "tag2"]),
        ("tag1, tag2", ["tag1", "tag2"]),  # Whitespace after comma is stripped
        ("tag1,,tag2", ["tag1", "tag2"]),  # Empty tags are filtered
        # Tags with leading '#' characters - these should be stripped
        (["#tag1", "##tag2"], ["tag1", "tag2"]),
        ("#tag1,##tag2", ["tag1", "tag2"]),
        (["tag1", "#tag2", "##tag3"], ["tag1", "tag2", "tag3"]),
        # Mixed whitespace and '#' characters
        ([" #tag1 ", " ##tag2 "], ["tag1", "tag2"]),
        (" #tag1 , ##tag2 ", ["tag1", "tag2"]),
        # JSON stringified arrays (common AI assistant issue)
        ('["tag1", "tag2", "tag3"]', ["tag1", "tag2", "tag3"]),
        ('["system", "overview", "reference"]', ["system", "overview", "reference"]),
        ('["#tag1", "##tag2"]', ["tag1", "tag2"]),  # JSON array with hash prefixes
        ('[ "tag1" , "tag2" ]', ["tag1", "tag2"]),  # JSON array with extra spaces
    ],
)
def test_parse_tags(input_tags: Union[List[str], str, None], expected: List[str]) -> None:
    """Test tag parsing with various input formats."""
    result = parse_tags(input_tags)
    assert result == expected


def test_parse_tags_special_case() -> None:
    """Test parsing from non-string, non-list types."""

    # Test with custom object that has __str__ method
    class TagObject:
        def __str__(self) -> str:
            return "tag1,tag2"

    result = parse_tags(TagObject())  # pyright: ignore [reportArgumentType]
    assert result == ["tag1", "tag2"]


def test_parse_tags_invalid_json() -> None:
    """Test that invalid JSON strings fall back to comma-separated parsing."""
    # Invalid JSON should fall back to comma-separated parsing
    result = parse_tags("[invalid json")
    assert result == ["[invalid json"]  # Treated as single tag

    result = parse_tags("[tag1, tag2]")  # Valid bracket format but not JSON
    assert result == ["[tag1", "tag2]"]  # Split by comma

    result = parse_tags('["tag1", "tag2"')  # Incomplete JSON
    assert result == ['["tag1"', '"tag2"']  # Fall back to comma separation
