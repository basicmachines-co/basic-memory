"""Tests for file utilities."""

import os
import time
import unicodedata
import random
import stat
import string
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from basic_memory.file_utils import (
    FileError,
    FileWriteError,
    ParseError,
    compute_checksum,
    file_created_timestamp,
    has_frontmatter,
    parse_frontmatter,
    remove_frontmatter,
    sanitize_for_filename,
    sanitize_for_directory,
    write_file_atomic,
    write_file_atomic_bytes,
)

# Skip marker for tests that use Unix-specific commands (cat, sh, sleep, /dev/null)
skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32", reason="Test uses Unix-specific commands not available on Windows"
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
        await compute_checksum(cast(Any, object()))


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
async def test_atomic_writes_preserve_legacy_staging_names(tmp_path: Path):
    text_target = tmp_path / "note.md"
    bytes_target = tmp_path / "index.md"
    text_staging_name = tmp_path / "note.tmp"
    bytes_staging_name = tmp_path / "index.tmp"
    text_staging_name.write_text("user text\n", encoding="utf-8")
    bytes_staging_name.write_bytes(b"user bytes\n")

    await write_file_atomic(text_target, "new text\n")
    await write_file_atomic_bytes(bytes_target, b"new bytes\n")

    assert text_target.read_text(encoding="utf-8") == "new text\n"
    assert bytes_target.read_bytes() == b"new bytes\n"
    assert text_staging_name.read_text(encoding="utf-8") == "user text\n"
    assert bytes_staging_name.read_bytes() == b"user bytes\n"


@skip_on_windows
@pytest.mark.asyncio
async def test_atomic_writes_preserve_existing_modes_and_umask_defaults(tmp_path: Path):
    text_target = tmp_path / "note.md"
    bytes_target = tmp_path / "index.md"
    text_target.write_text("old text\n", encoding="utf-8")
    bytes_target.write_bytes(b"old bytes\n")
    readonly_text_target = tmp_path / "readonly-note.md"
    readonly_bytes_target = tmp_path / "readonly-index.md"
    readonly_text_target.write_text("old readonly text\n", encoding="utf-8")
    readonly_bytes_target.write_bytes(b"old readonly bytes\n")
    text_target.chmod(0o664)
    bytes_target.chmod(0o640)
    readonly_text_target.chmod(0o444)
    readonly_bytes_target.chmod(0o444)
    reference = tmp_path / "reference.md"
    reference.touch()
    new_target = tmp_path / "new.md"

    await write_file_atomic(text_target, "new text\n")
    await write_file_atomic_bytes(bytes_target, b"new bytes\n")
    await write_file_atomic(readonly_text_target, "new readonly text\n")
    await write_file_atomic_bytes(readonly_bytes_target, b"new readonly bytes\n")
    await write_file_atomic_bytes(new_target, b"new file\n")

    assert stat.S_IMODE(text_target.stat().st_mode) == 0o664
    assert stat.S_IMODE(bytes_target.stat().st_mode) == 0o640
    assert readonly_text_target.read_text(encoding="utf-8") == "new readonly text\n"
    assert readonly_bytes_target.read_bytes() == b"new readonly bytes\n"
    assert stat.S_IMODE(readonly_text_target.stat().st_mode) == 0o444
    assert stat.S_IMODE(readonly_bytes_target.stat().st_mode) == 0o444
    assert stat.S_IMODE(new_target.stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)


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


def test_has_frontmatter_requires_line_anchored_fences():
    """Inline `---` must not be mistaken for frontmatter fences (issue #972)."""
    # Exact one-line repro from the issue: `\n` are literal backslash-n, not newlines,
    # so the whole string is a single line that merely starts with `---`.
    one_line = r"---\nstatus: active\n---\nDiscussed Q3 roadmap with Anthony."
    assert not has_frontmatter(one_line)

    # An inline `---` later in the first line is not an opening fence either.
    assert not has_frontmatter("--- not really frontmatter --- still content")

    # Opening fence with no closing fence on its own line is not frontmatter.
    assert not has_frontmatter("---\ntitle: Test\nbody without closing fence")

    # A fence with trailing whitespace is still a valid fence.
    assert has_frontmatter("---  \ntitle: Test\n---  \ncontent")


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


def test_frontmatter_helpers_ignore_inline_fences():
    """Single-line content starting with `---` is passed through unchanged (issue #972).

    Previously the loose substring/split logic merged a garbage `\\nstatus` YAML key
    into the note and silently stripped the inline `---...---` segment from the body.
    """
    one_line = r"---\nstatus: active\n---\nDiscussed Q3 roadmap with Anthony."

    # Not detected as frontmatter...
    assert not has_frontmatter(one_line)
    # ...so parsing raises rather than inventing a `\nstatus` key...
    with pytest.raises(ParseError):
        parse_frontmatter(one_line)
    # ...and the body survives intact through the merge path's removal step.
    assert remove_frontmatter(one_line) == one_line

    # An inline `---` later in the first line is also left untouched.
    inline = "intro --- not frontmatter --- outro"
    assert remove_frontmatter(inline) == inline

    # Valid line-anchored frontmatter with trailing whitespace on the fences parses
    # and is removed identically to clean fences.
    padded = "---  \ntitle: Test\n---  \ncontent"
    assert parse_frontmatter(padded) == {"title": "Test"}
    assert remove_frontmatter(padded) == "content"


def test_sanitize_for_filename_removes_invalid_characters():
    # Test all invalid characters listed in the regex
    invalid_chars = '<>:"|?*'

    # All invalid characters should be replaced
    for char in invalid_chars:
        text = get_random_word(length=12, necessary_char=char)
        sanitized_text = sanitize_for_filename(text)

        assert char not in sanitized_text


def test_sanitize_for_filename_strips_trailing_periods():
    """Trailing periods cause double-dot filenames like 'hi-everyone..md'.

    This was a production bug where title "Hi everyone." produced file path
    "hi-everyone..md" which failed path traversal validation.
    """
    assert sanitize_for_filename("Hi everyone.") == "Hi everyone"
    assert sanitize_for_filename("test...") == "test"
    assert sanitize_for_filename(".hidden") == "hidden"
    assert sanitize_for_filename("...dots...") == "dots"
    assert sanitize_for_filename("normal title") == "normal title"


@pytest.mark.parametrize(
    "input_directory,expected",
    [
        ("", ""),  # Empty string
        ("   ", ""),  # Whitespace only
        ("my-directory", "my-directory"),  # Simple directory
        ("my/directory", "my/directory"),  # Nested directory
        ("my//directory", "my/directory"),  # Double slash compressed
        ("my\\\\directory", "my/directory"),  # Windows-style double backslash compressed
        ("my/directory/", "my/directory"),  # Trailing slash removed
        ("/my/directory", "my/directory"),  # Leading slash removed
        ("./my/directory", "my/directory"),  # Leading ./ removed
        ("my<>directory", "mydirectory"),  # Special chars removed
        ("my:directory|test", "mydirectorytest"),  # More special chars removed
        ("my_directory-1", "my_directory-1"),  # Allowed chars preserved
        ("my directory", "my directory"),  # Space preserved
        ("my/directory//sub//", "my/directory/sub"),  # Multiple compressions and trims
        ("my\\directory\\sub", "my/directory/sub"),  # Windows-style separators normalized
        ("my/directory<>:|?*sub", "my/directorysub"),  # All invalid chars removed
        ("////my////directory////", "my/directory"),  # Excessive leading/trailing/multiple slashes
    ],
)
def test_sanitize_for_directory_edge_cases(input_directory, expected):
    assert sanitize_for_directory(input_directory) == expected


# =============================================================================
# BOM handling tests
# =============================================================================


class TestBOMHandling:
    """Test handling of Byte Order Mark (BOM) in frontmatter.

    BOM characters can be present in files created on Windows or copied
    from certain sources. They should not break frontmatter detection
    or parsing. See issue #452.
    """

    def test_has_frontmatter_with_bom(self):
        """Test that has_frontmatter handles BOM correctly."""
        # Content with UTF-8 BOM
        content_with_bom = "\ufeff---\ntitle: Test\n---\nContent"
        assert has_frontmatter(content_with_bom), "Should detect frontmatter even with BOM"

    def test_has_frontmatter_with_bom_and_windows_crlf(self):
        """Test BOM with Windows line endings."""
        content = "\ufeff---\r\ntitle: Test\r\n---\r\nContent"
        assert has_frontmatter(content), "Should detect frontmatter with BOM and CRLF"

    def test_parse_frontmatter_with_bom(self):
        """Test that parse_frontmatter handles BOM correctly."""
        content_with_bom = "\ufeff---\ntitle: Test Title\ntype: note\n---\nContent"
        result = parse_frontmatter(content_with_bom)
        assert result["title"] == "Test Title"
        assert result["type"] == "note"

    def test_remove_frontmatter_with_bom(self):
        """Test that remove_frontmatter handles BOM correctly."""
        content_with_bom = "\ufeff---\ntitle: Test\n---\nContent here"
        result = remove_frontmatter(content_with_bom)
        assert result == "Content here"


def test_remove_frontmatter_strip_false_preserves_body_exactly():
    """strip=False returns the body verbatim so frontmatter-only rewrites don't reflow it."""
    content = "---\nstatus: draft\n---\n\nBody with hard break  \n"
    assert remove_frontmatter(content, strip=False) == "\nBody with hard break  \n"


def test_remove_frontmatter_strip_false_without_frontmatter_returns_content_unchanged():
    text = "  leading spaces and trailing newline\n"
    assert remove_frontmatter(text, strip=False) == text


def test_remove_frontmatter_keeps_a_fenced_block_that_is_not_a_yaml_mapping():
    """A letterhead between horizontal rules, a bare scalar, or a list is body, not
    frontmatter; no frontmatter reader accepts it, so search must not lose it (#1451)."""
    letterhead = "---\n**ACME LEGAL PLLC**\n123 Main St\n---\n\nDear counsel,\n"
    scalar = "---\nACME LEGAL PLLC\n---\n\nDear counsel,\n"
    listing = "---\n- one\n- two\n---\n\nDear counsel,\n"

    assert remove_frontmatter(letterhead) == letterhead.strip()
    assert remove_frontmatter(scalar) == scalar.strip()
    assert remove_frontmatter(listing) == listing.strip()
    assert remove_frontmatter(scalar, strip=False) == scalar
    # An empty block is still frontmatter: every reader accepts it as {}.
    assert remove_frontmatter("---\n---\n\nbody\n") == "body"


def test_sanitize_for_directory_keeps_combining_marks():
    """An NFD name keeps its accents; dropping U+0301 turned 'café' into 'cafe' (#1796)."""
    nfd = unicodedata.normalize("NFD", "café/menús")

    assert sanitize_for_directory(nfd) == nfd


def test_created_timestamp_is_not_later_than_an_edit_then_move(tmp_path: Path) -> None:
    """Editing a note and then moving it must not date its creation after the edit (#2004)."""
    original = tmp_path / "Three.md"
    original.write_text("# Three\n", encoding="utf-8")
    time.sleep(0.02)
    with original.open("a", encoding="utf-8") as note:
        note.write("Closed.\n")
    time.sleep(0.02)
    # The rename resets st_ctime on POSIX, after the append set st_mtime.
    archived = original.rename(tmp_path / "Three-archived.md")

    stat_result = archived.stat()

    assert file_created_timestamp(stat_result) <= stat_result.st_mtime


def test_created_timestamp_without_birthtime_takes_the_earlier_of_ctime_and_mtime() -> None:
    # A tuple-built stat_result carries no birth time on any platform.
    renamed_after_edit = os.stat_result((0,) * 7 + (1.0, 5.0, 9.0))
    assert file_created_timestamp(renamed_after_edit) == 5.0

    edited_after_chmod = os.stat_result((0,) * 7 + (1.0, 9.0, 5.0))
    assert file_created_timestamp(edited_after_chmod) == 5.0
