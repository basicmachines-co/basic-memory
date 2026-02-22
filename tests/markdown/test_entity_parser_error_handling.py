"""Tests for entity parser error handling (issues #184 and #185)."""

import pytest
from textwrap import dedent
from types import SimpleNamespace

from basic_memory.markdown.entity_parser import EntityParser


@pytest.mark.asyncio
async def test_parse_file_with_malformed_yaml_frontmatter(tmp_path):
    """Test that files with malformed YAML frontmatter are parsed gracefully (issue #185).

    This reproduces the production error where block sequence entries cause YAML parsing to fail.
    The parser should handle the error gracefully and treat the file as plain markdown.
    """
    # Create a file with malformed YAML frontmatter
    test_file = tmp_path / "malformed.md"
    content = dedent(
        """
        ---
        title: Group Chat Texts
        tags:
          - family    # Line 5, column 7 - this syntax can fail in certain YAML contexts
          - messages
        type: note
        ---
        # Group Chat Texts

        Content here
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file - should not raise YAMLError
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should successfully parse, treating as plain markdown if YAML fails
    assert result is not None
    # If YAML parsing succeeded, verify expected values
    # If it failed, it should have defaults
    assert result.frontmatter.title is not None
    assert result.frontmatter.type is not None


@pytest.mark.asyncio
async def test_parse_file_with_completely_invalid_yaml(tmp_path):
    """Test that files with completely invalid YAML are handled gracefully (issue #185).

    This tests the extreme case where YAML parsing completely fails.
    """
    # Create a file with completely broken YAML
    test_file = tmp_path / "broken_yaml.md"
    content = dedent(
        """
        ---
        title: Invalid YAML
        this is: [not, valid, yaml
        missing: closing bracket
        ---
        # Content

        This file has broken YAML frontmatter.
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file - should not raise exception
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should successfully parse with defaults
    assert result is not None
    assert result.frontmatter.title == "broken_yaml"  # Default from filename
    assert result.frontmatter.type == "note"  # Default type
    # Content should include the whole file since frontmatter parsing failed
    assert "# Content" in result.content


@pytest.mark.asyncio
async def test_parse_file_without_note_type(tmp_path):
    """Test that files without note_type get a default value (issue #184).

    This reproduces the NOT NULL constraint error where note_type was missing.
    """
    # Create a file without note_type in frontmatter
    test_file = tmp_path / "no_type.md"
    content = dedent(
        """
        ---
        title: The Invisible Weight of Mental Habits
        ---
        # The Invisible Weight of Mental Habits

        An article about mental habits.
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should have default note_type
    assert result is not None
    assert result.frontmatter.type == "note"  # Default type applied
    assert result.frontmatter.title == "The Invisible Weight of Mental Habits"


@pytest.mark.asyncio
async def test_parse_file_with_empty_frontmatter(tmp_path):
    """Test that files with empty frontmatter get defaults (issue #184)."""
    # Create a file with empty frontmatter
    test_file = tmp_path / "empty_frontmatter.md"
    content = dedent(
        """
        ---
        ---
        # Content

        This file has empty frontmatter.
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should have defaults
    assert result is not None
    assert result.frontmatter.type == "note"  # Default type
    assert result.frontmatter.title == "empty_frontmatter"  # Default from filename


@pytest.mark.asyncio
async def test_parse_file_without_frontmatter(tmp_path):
    """Test that files without any frontmatter get defaults (issue #184)."""
    # Create a file with no frontmatter at all
    test_file = tmp_path / "no_frontmatter.md"
    content = dedent(
        """
        # Just Content

        This file has no frontmatter at all.
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should have defaults
    assert result is not None
    assert result.frontmatter.type == "note"  # Default type
    assert result.frontmatter.title == "no_frontmatter"  # Default from filename


@pytest.mark.asyncio
async def test_parse_file_with_null_note_type(tmp_path):
    """Test that files with explicit null note_type get default (issue #184)."""
    # Create a file with null/None note_type
    test_file = tmp_path / "null_type.md"
    content = dedent(
        """
        ---
        title: Test File
        type: null
        ---
        # Content
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should have default type even when explicitly set to null
    assert result is not None
    assert result.frontmatter.type == "note"  # Default type applied
    assert result.frontmatter.title == "Test File"


@pytest.mark.asyncio
async def test_parse_file_with_null_title(tmp_path):
    """Test that files with explicit null title get default from filename (issue #387)."""
    # Create a file with null title
    test_file = tmp_path / "null_title.md"
    content = dedent(
        """
        ---
        title: null
        type: note
        ---
        # Content
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should have default title from filename even when explicitly set to null
    assert result is not None
    assert result.frontmatter.title == "null_title"  # Default from filename
    assert result.frontmatter.type == "note"


@pytest.mark.asyncio
async def test_parse_file_with_empty_title(tmp_path):
    """Test that files with empty title get default from filename (issue #387)."""
    # Create a file with empty title
    test_file = tmp_path / "empty_title.md"
    content = dedent(
        """
        ---
        title:
        type: note
        ---
        # Content
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should have default title from filename when title is empty
    assert result is not None
    assert result.frontmatter.title == "empty_title"  # Default from filename
    assert result.frontmatter.type == "note"


@pytest.mark.asyncio
async def test_parse_file_with_string_none_title(tmp_path):
    """Test that files with string 'None' title get default from filename (issue #387)."""
    # Create a file with string "None" as title (common in templates)
    test_file = tmp_path / "template_file.md"
    content = dedent(
        """
        ---
        title: "None"
        type: note
        ---
        # Content
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should have default title from filename when title is string "None"
    assert result is not None
    assert result.frontmatter.title == "template_file"  # Default from filename
    assert result.frontmatter.type == "note"


@pytest.mark.asyncio
async def test_parse_valid_file_still_works(tmp_path):
    """Test that valid files with proper frontmatter still parse correctly."""
    # Create a valid file
    test_file = tmp_path / "valid.md"
    content = dedent(
        """
        ---
        title: Valid File
        type: knowledge
        tags:
          - test
          - valid
        ---
        # Valid File

        This is a properly formatted file.
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # Should parse correctly with all values
    assert result is not None
    assert result.frontmatter.title == "Valid File"
    assert result.frontmatter.type == "knowledge"
    assert result.frontmatter.tags == ["test", "valid"]


@pytest.mark.asyncio
async def test_invalid_yaml_does_not_add_metadata_key(tmp_path):
    """Test that invalid YAML doesn't create spurious 'metadata' key in frontmatter (issue #528).

    This tests a bug where `frontmatter.Post(content, metadata={})` was used incorrectly.
    The `metadata={}` kwarg creates a KEY called "metadata" in the metadata dict,
    rather than setting the metadata to an empty dict.

    This caused files with invalid YAML to get `metadata: {}` in their frontmatter output,
    which is incorrect and confusing.
    """
    # Create a file with completely broken YAML that will trigger the fallback path
    test_file = tmp_path / "broken_yaml.md"
    content = dedent(
        """
        ---
        title: Invalid YAML
        this is: [not, valid, yaml
        missing: closing bracket
        ---
        # Content

        This file has broken YAML frontmatter.
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # The metadata dict should NOT contain a "metadata" key
    # This was the bug: frontmatter.Post(content, metadata={}) creates {"metadata": {}}
    assert "metadata" not in result.frontmatter.metadata, (
        "Frontmatter metadata should not contain a 'metadata' key. "
        "This indicates the bug where Post(content, metadata={}) was used incorrectly."
    )

    # Should still have the expected defaults
    assert result.frontmatter.title == "broken_yaml"
    assert result.frontmatter.type == "note"


@pytest.mark.asyncio
async def test_frontmatter_roundtrip_preserves_user_metadata(tmp_path):
    """Test that parsing and re-serializing frontmatter preserves user fields (issue #528).

    Users reported that after cloud sync, their custom frontmatter fields like 'citekey'
    were being lost and replaced with defaults. This test ensures user metadata is preserved.
    """
    from basic_memory.file_utils import dump_frontmatter
    import frontmatter

    # Create a file with user's custom frontmatter (like the bug report)
    test_file = tmp_path / "litnote.md"
    content = dedent(
        """
        ---
        title: "My Document Title"
        type: litnote
        tags:
          - research
          - methodology
        citekey: authorTitleYear2024
        ---

        # Content here...
        """
    ).strip()
    test_file.write_text(content)

    # Parse the file
    parser = EntityParser(tmp_path)
    result = await parser.parse_file(test_file)

    # User's custom fields should be preserved
    assert result.frontmatter.title == "My Document Title"
    assert result.frontmatter.type == "litnote"  # NOT overwritten to "note"
    assert "citekey" in result.frontmatter.metadata
    assert result.frontmatter.metadata["citekey"] == "authorTitleYear2024"

    # Simulate what write_frontmatter does
    post = frontmatter.Post(result.content, **result.frontmatter.metadata)
    output = dump_frontmatter(post)

    # The output should NOT have duplicate frontmatter or metadata: {} key
    assert output.count("---") == 2, (
        "Should have exactly one frontmatter block (two --- delimiters)"
    )
    assert "metadata:" not in output, "Should not have 'metadata:' key in output"
    assert "citekey: authorTitleYear2024" in output, "User's citekey should be preserved"
    assert "type: litnote" in output, "User's type should be preserved"


@pytest.mark.asyncio
async def test_schema_to_markdown_empty_metadata_no_metadata_key():
    """Regression test: schema_to_markdown with entity_metadata={} must not emit 'metadata:' in YAML.

    The bug was that an empty entity_metadata dict would still call post.metadata.update({}),
    which is harmless, but prior versions could produce a spurious 'metadata: {}' key via
    incorrect Post() construction. This test ensures the guard (`if entity_metadata:`) prevents
    that — an empty dict is falsy and should skip the update entirely.
    """
    from basic_memory.markdown.utils import schema_to_markdown
    from basic_memory.file_utils import dump_frontmatter

    schema = SimpleNamespace(
        title="Empty Metadata Test",
        note_type="note",
        permalink="empty-metadata-test",
        content="# Empty Metadata Test\n\nSome content.",
        entity_metadata={},
    )

    post = await schema_to_markdown(schema)
    output = dump_frontmatter(post)

    # The YAML output should NOT contain a 'metadata:' key
    assert "metadata:" not in output, (
        f"Empty entity_metadata should not produce 'metadata:' in YAML output.\nGot:\n{output}"
    )
    # Should still have the expected frontmatter fields
    assert "title: Empty Metadata Test" in output
    assert "type: note" in output
    assert "permalink: empty-metadata-test" in output
