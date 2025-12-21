"""Tests for file utilities."""

from pathlib import Path

import pytest
import random
import string

from basic_memory.config import BasicMemoryConfig
from basic_memory.file_utils import (
    FileError,
    FileWriteError,
    ParseError,
    compute_checksum,
    format_file,
    has_frontmatter,
    parse_frontmatter,
    remove_frontmatter,
    sanitize_for_filename,
    sanitize_for_folder,
    write_file_atomic,
)


def get_random_word(length: int = 12, necessary_char: str | None = None) -> str:
    letters = string.ascii_lowercase
    word_chars = [random.choice(letters) for i in range(length)]

    if necessary_char and length > 0:
        # Replace a character at a random position with the necessary character
        random_pos = random.randint(0, length - 1)
        word_chars[random_pos] = necessary_char

    return "".join(word_chars)


@pytest.mark.asyncio
async def test_compute_checksum():
    """Test checksum computation."""
    content = "test content"
    checksum = await compute_checksum(content)
    assert isinstance(checksum, str)
    assert len(checksum) == 64  # SHA-256 produces 64 char hex string


@pytest.mark.asyncio
async def test_compute_checksum_error():
    """Test checksum error handling."""
    with pytest.raises(FileError):
        # Try to hash an object that can't be encoded
        await compute_checksum(object())  # pyright: ignore [reportArgumentType]


@pytest.mark.asyncio
async def test_write_file_atomic(tmp_path: Path):
    """Test atomic file writing."""
    test_file = tmp_path / "test.txt"
    content = "test content"

    await write_file_atomic(test_file, content)
    assert test_file.exists()
    assert test_file.read_text(encoding="utf-8") == content

    # Temp file should be cleaned up
    assert not test_file.with_suffix(".tmp").exists()


@pytest.mark.asyncio
async def test_write_file_atomic_error(tmp_path: Path):
    """Test atomic write error handling."""
    # Try to write to a directory that doesn't exist
    test_file = tmp_path / "nonexistent" / "test.txt"

    with pytest.raises(FileWriteError):
        await write_file_atomic(test_file, "test content")


def test_has_frontmatter():
    """Test frontmatter detection."""
    # Valid frontmatter
    assert has_frontmatter("""---
title: Test
---
content""")

    # Just content
    assert not has_frontmatter("Just content")

    # Empty content
    assert not has_frontmatter("")

    # Just delimiter
    assert not has_frontmatter("---")

    # Delimiter not at start
    assert not has_frontmatter("""
Some text
---
title: Test
---""")

    # Invalid format
    assert not has_frontmatter("--title: test--")


def test_parse_frontmatter():
    """Test parsing frontmatter."""
    # Valid frontmatter
    content = """---
title: Test
tags:
  - a
  - b
---
content"""

    result = parse_frontmatter(content)
    assert result == {"title": "Test", "tags": ["a", "b"]}

    # Empty frontmatter
    content = """---
---
content"""
    result = parse_frontmatter(content)
    assert result == {} or result == {}  # Handle both None and empty dict cases

    # Invalid YAML syntax
    with pytest.raises(ParseError) as exc:
        parse_frontmatter("""---
[: invalid yaml syntax :]
---
content""")
    assert "Invalid YAML in frontmatter" in str(exc.value)

    # Non-dict YAML content
    with pytest.raises(ParseError) as exc:
        parse_frontmatter("""---
- just
- a
- list
---
content""")
    assert "Frontmatter must be a YAML dictionary" in str(exc.value)

    # No frontmatter
    with pytest.raises(ParseError):
        parse_frontmatter("Just content")

    # Incomplete frontmatter
    with pytest.raises(ParseError):
        parse_frontmatter("""---
title: Test""")


def test_remove_frontmatter():
    """Test removing frontmatter."""
    # With frontmatter
    content = """---
title: Test
---
test content"""
    assert remove_frontmatter(content) == "test content"

    # No frontmatter
    content = "test content"
    assert remove_frontmatter(content) == "test content"

    # Only frontmatter
    content = """---
title: Test
---
"""
    assert remove_frontmatter(content) == ""

    # Invalid frontmatter - missing closing delimiter
    with pytest.raises(ParseError) as exc:
        remove_frontmatter("""---
title: Test""")
    assert "Invalid frontmatter format" in str(exc.value)


@pytest.mark.asyncio
def test_sanitize_for_filename_removes_invalid_characters():
    # Test all invalid characters listed in the regex
    invalid_chars = '<>:"|?*'

    # All invalid characters should be replaced
    for char in invalid_chars:
        text = get_random_word(length=12, necessary_char=char)
        sanitized_text = sanitize_for_filename(text)

        assert char not in sanitized_text


@pytest.mark.parametrize(
    "input_folder,expected",
    [
        ("", ""),  # Empty string
        ("   ", ""),  # Whitespace only
        ("my-folder", "my-folder"),  # Simple folder
        ("my/folder", "my/folder"),  # Nested folder
        ("my//folder", "my/folder"),  # Double slash compressed
        ("my\\\\folder", "my/folder"),  # Windows-style double backslash compressed
        ("my/folder/", "my/folder"),  # Trailing slash removed
        ("/my/folder", "my/folder"),  # Leading slash removed
        ("./my/folder", "my/folder"),  # Leading ./ removed
        ("my<>folder", "myfolder"),  # Special chars removed
        ("my:folder|test", "myfoldertest"),  # More special chars removed
        ("my_folder-1", "my_folder-1"),  # Allowed chars preserved
        ("my folder", "my folder"),  # Space preserved
        ("my/folder//sub//", "my/folder/sub"),  # Multiple compressions and trims
        ("my\\folder\\sub", "my/folder/sub"),  # Windows-style separators normalized
        ("my/folder<>:|?*sub", "my/foldersub"),  # All invalid chars removed
        ("////my////folder////", "my/folder"),  # Excessive leading/trailing/multiple slashes
    ],
)
def test_sanitize_for_folder_edge_cases(input_folder, expected):
    assert sanitize_for_folder(input_folder) == expected


# =============================================================================
# format_file tests
# =============================================================================


@pytest.mark.asyncio
async def test_format_file_disabled_by_default(tmp_path: Path):
    """Test that format_file returns None when format_on_save is False (default)."""
    test_file = tmp_path / "test.md"
    test_file.write_text("# Test\n")

    config = BasicMemoryConfig()
    assert config.format_on_save is False

    result = await format_file(test_file, config)
    assert result is None


@pytest.mark.asyncio
async def test_format_file_no_formatter_configured(tmp_path: Path):
    """Test that format_file returns None when no formatter is configured for the extension."""
    test_file = tmp_path / "test.md"
    test_file.write_text("# Test\n")

    config = BasicMemoryConfig(format_on_save=True)
    # No formatter_command or formatters configured

    result = await format_file(test_file, config)
    assert result is None


@pytest.mark.asyncio
async def test_format_file_with_global_formatter(tmp_path: Path):
    """Test formatting with global formatter_command."""
    test_file = tmp_path / "test.md"
    original_content = "# Test\n"
    test_file.write_text(original_content)

    # Use a simple formatter that just echoes content (cat)
    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="cat {file}",  # This doesn't modify the file but runs successfully
    )

    result = await format_file(test_file, config)
    assert result == original_content


@pytest.mark.asyncio
async def test_format_file_with_extension_specific_formatter(tmp_path: Path):
    """Test formatting with extension-specific formatter."""
    test_file = tmp_path / "test.json"
    original_content = '{"key": "value"}'
    test_file.write_text(original_content)

    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="echo global",  # This should NOT be used
        formatters={"json": "cat {file}"},  # Extension-specific should be used
    )

    result = await format_file(test_file, config)
    assert result == original_content


@pytest.mark.asyncio
async def test_format_file_extension_specific_overrides_global(tmp_path: Path):
    """Test that extension-specific formatter takes precedence over global."""
    test_file = tmp_path / "test.md"
    original_content = "# Test\n"
    test_file.write_text(original_content)

    # Use different commands to verify which one is used
    # Since cat just reads the file, we can tell which was used by the content
    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="cat /dev/null",  # Would return empty
        formatters={"md": "cat {file}"},  # Should return original content
    )

    result = await format_file(test_file, config)
    assert result == original_content


@pytest.mark.asyncio
async def test_format_file_falls_back_to_global(tmp_path: Path):
    """Test that global formatter is used when no extension-specific one exists."""
    test_file = tmp_path / "test.txt"  # No extension-specific formatter for .txt
    original_content = "Some text\n"
    test_file.write_text(original_content)

    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="cat {file}",
        formatters={"md": "echo wrong"},  # Only for .md, not .txt
    )

    result = await format_file(test_file, config)
    assert result == original_content


@pytest.mark.asyncio
async def test_format_file_handles_nonexistent_formatter(tmp_path: Path):
    """Test that format_file handles missing formatter executable gracefully."""
    test_file = tmp_path / "test.md"
    test_file.write_text("# Test\n")

    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="nonexistent_formatter_executable_12345 {file}",
    )

    result = await format_file(test_file, config)
    assert result is None  # Should return None on error


@pytest.mark.asyncio
async def test_format_file_handles_timeout(tmp_path: Path):
    """Test that format_file handles formatter timeout gracefully."""
    test_file = tmp_path / "test.md"
    test_file.write_text("# Test\n")

    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="sleep 10",  # Will timeout
        formatter_timeout=0.1,  # Very short timeout
    )

    result = await format_file(test_file, config)
    assert result is None  # Should return None on timeout


@pytest.mark.asyncio
async def test_format_file_handles_nonzero_exit(tmp_path: Path):
    """Test that format_file handles non-zero exit codes gracefully."""
    test_file = tmp_path / "test.md"
    original_content = "# Test\n"
    test_file.write_text(original_content)

    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="sh -c 'exit 1'",  # Non-zero exit
    )

    result = await format_file(test_file, config)
    # Should still return file content even with non-zero exit
    assert result == original_content


@pytest.mark.asyncio
async def test_format_file_returns_modified_content(tmp_path: Path):
    """Test that format_file returns the modified file content after formatting."""
    test_file = tmp_path / "test.md"
    original_content = "original content"
    test_file.write_text(original_content)

    # This formatter modifies the file to contain different content
    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="sh -c 'echo modified > {file}'",
    )

    result = await format_file(test_file, config)
    assert result == "modified\n"
    assert test_file.read_text() == "modified\n"


@pytest.mark.asyncio
async def test_format_file_with_spaces_in_path(tmp_path: Path):
    """Test formatting files with spaces in path."""
    subdir = tmp_path / "path with spaces"
    subdir.mkdir()
    test_file = subdir / "my file.md"
    original_content = "# Test\n"
    test_file.write_text(original_content)

    config = BasicMemoryConfig(
        format_on_save=True,
        formatter_command="cat {file}",
    )

    result = await format_file(test_file, config)
    assert result == original_content
