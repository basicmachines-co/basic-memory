"""
Integration tests for write_note MCP tool.

Tests various scenarios including note creation, content formatting,
tag handling, and error conditions.
"""

import pytest
from basic_memory.mcp.tools import write_note, read_note


@pytest.mark.asyncio
async def test_write_simple_note(app):
    """Test creating a simple note with basic content."""
    result = await write_note(
        title="Simple Note",
        folder="basic",
        content="# Simple Note\n\nThis is a simple note for testing.",
        tags="simple,test",
    )
    
    assert result
    assert "file_path: basic/Simple Note.md" in result
    assert "permalink: basic/simple-note" in result
    assert "checksum:" in result


@pytest.mark.asyncio
async def test_write_note_with_complex_content(app):
    """Test creating a note with complex markdown content."""
    complex_content = """# Complex Note

This note has various markdown elements:

## Subsection

- List item 1
- List item 2

### Code Block

```python
def hello():
    print("Hello, World!")
```

> This is a blockquote

[Link to something](https://example.com)

| Table | Header |
|-------|--------|
| Cell  | Data   |
"""
    
    result = await write_note(
        title="Complex Content Note",
        folder="advanced",
        content=complex_content,
        tags="complex,markdown,testing",
    )
    
    assert result
    assert "file_path: advanced/Complex Content Note.md" in result
    assert "permalink: advanced/complex-content-note" in result
    
    # Verify content was saved correctly by reading it back
    read_result = await read_note("advanced/complex-content-note")
    assert "def hello():" in read_result
    assert "| Table | Header |" in read_result


@pytest.mark.asyncio
async def test_write_note_with_observations_and_relations(app):
    """Test creating a note with knowledge graph elements."""
    content_with_kg = """# Research Topic

## Overview
This is a research topic about artificial intelligence.

## Observations
- [method] Uses machine learning algorithms
- [finding] Shows promising results in NLP tasks
- [limitation] Requires large amounts of training data

## Relations
- related_to [[Machine Learning]]
- implements [[Neural Networks]]
- used_in [[Natural Language Processing]]

## Notes
Further research needed on scalability.
"""
    
    result = await write_note(
        title="Research Topic",
        folder="research",
        content=content_with_kg,
        tags="research,ai,ml",
    )
    
    assert result
    assert "file_path: research/Research Topic.md" in result
    assert "permalink: research/research-topic" in result
    
    # Verify knowledge graph elements were processed
    read_result = await read_note("research/research-topic")
    assert "- [method]" in read_result
    assert "related_to [[Machine Learning]]" in read_result


@pytest.mark.asyncio
async def test_write_note_nested_folders(app):
    """Test creating notes in nested folder structures."""
    result = await write_note(
        title="Deep Note",
        folder="level1/level2/level3",
        content="# Deep Note\n\nThis note is in a deeply nested folder.",
        tags="nested,deep",
    )
    
    assert result
    assert "file_path: level1/level2/level3/Deep Note.md" in result
    assert "permalink: level1/level2/level3/deep-note" in result


@pytest.mark.asyncio
async def test_write_note_root_folder(app):
    """Test creating a note in the root folder."""
    result = await write_note(
        title="Root Note",
        folder="",
        content="# Root Note\n\nThis note is in the root folder.",
        tags="root",
    )
    
    assert result
    assert "file_path: Root Note.md" in result
    assert "permalink: root-note" in result


@pytest.mark.asyncio
async def test_write_note_special_characters_in_title(app):
    """Test creating notes with special characters in titles."""
    result = await write_note(
        title="Note with Special: Characters & Symbols!",
        folder="special",
        content="# Special Characters\n\nTesting special characters in title.",
        tags="special,characters",
    )
    
    assert result
    assert "file_path: special/Note with Special: Characters & Symbols!.md" in result
    # Permalink should be sanitized
    assert "permalink: special/note-with-special-characters-symbols" in result


@pytest.mark.asyncio
async def test_write_note_update_existing(app):
    """Test updating an existing note."""
    # Create initial note
    initial_result = await write_note(
        title="Update Test",
        folder="updates",
        content="# Initial Content\n\nOriginal content.",
        tags="initial",
    )
    
    assert "file_path: updates/Update Test.md" in initial_result
    
    # Update the same note
    updated_result = await write_note(
        title="Update Test",
        folder="updates",
        content="# Updated Content\n\nThis content has been updated.",
        tags="updated",
    )
    
    assert "file_path: updates/Update Test.md" in updated_result
    assert "Updated" in updated_result
    
    # Verify the content was actually updated
    read_result = await read_note("updates/update-test")
    assert "Updated Content" in read_result
    assert "Original content" not in read_result


@pytest.mark.asyncio
async def test_write_note_with_frontmatter_tags(app):
    """Test that tags are properly added to frontmatter."""
    result = await write_note(
        title="Tags Test",
        folder="tagging",
        content="# Tags Test\n\nTesting tag functionality.",
        tags="tag1,tag2,tag3",
    )
    
    assert result
    
    # Read back and verify tags in frontmatter
    read_result = await read_note("tagging/tags-test")
    assert "tags:" in read_result
    assert "#tag1" in read_result
    assert "#tag2" in read_result
    assert "#tag3" in read_result


@pytest.mark.asyncio
async def test_write_note_empty_content(app):
    """Test creating a note with minimal content."""
    result = await write_note(
        title="Empty Note",
        folder="minimal",
        content="",
        tags="empty",
    )
    
    assert result
    assert "file_path: minimal/Empty Note.md" in result
    
    # Should still create the note with frontmatter
    read_result = await read_note("minimal/empty-note")
    assert "title: Empty Note" in read_result


@pytest.mark.asyncio
async def test_write_note_no_tags(app):
    """Test creating a note without tags."""
    result = await write_note(
        title="No Tags Note",
        folder="notags",
        content="# No Tags\n\nThis note has no tags.",
        tags="",
    )
    
    assert result
    assert "file_path: notags/No Tags Note.md" in result
    
    # Verify note was created successfully
    read_result = await read_note("notags/no-tags-note")
    assert "# No Tags" in read_result