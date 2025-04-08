"""Tests for MarkdownProcessor.

Tests focus on the Read -> Modify -> Write pattern and content preservation.
"""

from datetime import datetime
from pathlib import Path

import pytest

from basic_memory.markdown.markdown_processor import DirtyFileError, MarkdownProcessor
from basic_memory.markdown.schemas import (
    EntityFrontmatter,
    EntityMarkdown,
    Observation,
    Relation,
)


@pytest.mark.asyncio
async def test_write_new_minimal_file(markdown_processor: MarkdownProcessor, tmp_path: Path):
    """Test creating new file with just title."""
    path = tmp_path / "test.md"

    # Create minimal markdown schema
    metadata = {}
    metadata["title"] = "Test Note"
    metadata["type"] = "note"
    metadata["permalink"] = "test"
    metadata["created"] = datetime(2024, 1, 1)
    metadata["modified"] = datetime(2024, 1, 1)
    metadata["tags"] = ["test"]
    markdown = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            metadata=metadata,
        ),
        content="",
    )

    # Write file
    await markdown_processor.write_file(path, markdown)

    # Read back and verify
    content = path.read_text(encoding="utf-8")
    assert "---" in content  # Has frontmatter
    assert "type: note" in content
    assert "permalink: test" in content
    assert "# Test Note" in content  # Added title
    assert "tags:" in content
    assert "- test" in content

    # Should not have empty sections
    assert "## Observations" not in content
    assert "## Relations" not in content


@pytest.mark.asyncio
async def test_write_new_file_with_content(markdown_processor: MarkdownProcessor, tmp_path: Path):
    """Test creating new file with content and sections."""
    path = tmp_path / "test.md"

    # Create markdown with content and sections
    markdown = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            type="note",
            permalink="test",
            title="Test Note",
            created=datetime(2024, 1, 1),
            modified=datetime(2024, 1, 1),
        ),
        content="# Custom Title\n\nMy content here.\nMultiple lines.",
        observations=[
            Observation(
                content="Test observation #test",
                category="tech",
                tags=["test"],
                context="test context",
            ),
        ],
        relations=[
            Relation(
                type="relates_to",
                target="other-note",
                context="test relation",
            ),
        ],
    )

    # Write file
    await markdown_processor.write_file(path, markdown)

    # Read back and verify
    content = path.read_text(encoding="utf-8")

    # Check content preserved exactly
    assert "# Custom Title" in content
    assert "My content here." in content
    assert "Multiple lines." in content

    # Check sections formatted correctly
    assert "- [tech] Test observation #test (test context)" in content
    assert "- relates_to [[other-note]] (test relation)" in content


@pytest.mark.asyncio
async def test_update_preserves_content(markdown_processor: MarkdownProcessor, tmp_path: Path):
    """Test that updating file preserves existing content."""
    path = tmp_path / "test.md"

    # Create initial file
    initial = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            type="note",
            permalink="test",
            title="Test Note",
            created=datetime(2024, 1, 1),
            modified=datetime(2024, 1, 1),
        ),
        content="# My Note\n\nOriginal content here.",
        observations=[
            Observation(content="First observation", category="note"),
        ],
    )

    checksum = await markdown_processor.write_file(path, initial)

    # Update with new observation
    updated = EntityMarkdown(
        frontmatter=initial.frontmatter,
        content=initial.content,  # Preserve original content
        observations=[
            initial.observations[0],  # Keep original observation
            Observation(content="Second observation", category="tech"),  # Add new one
        ],
    )

    # Update file
    await markdown_processor.write_file(path, updated, expected_checksum=checksum)

    # Read back and verify
    result = await markdown_processor.read_file(path)

    # Original content preserved
    assert "Original content here." in result.content

    # Both observations present
    assert len(result.observations) == 2
    assert any(o.content == "First observation" for o in result.observations)
    assert any(o.content == "Second observation" for o in result.observations)


@pytest.mark.asyncio
async def test_dirty_file_detection(markdown_processor: MarkdownProcessor, tmp_path: Path):
    """Test detection of file modifications."""
    path = tmp_path / "test.md"

    # Create initial file
    initial = EntityMarkdown(
        frontmatter=EntityFrontmatter(
            type="note",
            permalink="test",
            title="Test Note",
            created=datetime(2024, 1, 1),
            modified=datetime(2024, 1, 1),
        ),
        content="Initial content",
    )

    checksum = await markdown_processor.write_file(path, initial)

    # Modify file directly
    path.write_text(path.read_text(encoding="utf-8") + "\nModified!")

    # Try to update with old checksum
    update = EntityMarkdown(
        frontmatter=initial.frontmatter,
        content="New content",
    )

    # Should raise DirtyFileError
    with pytest.raises(DirtyFileError):
        await markdown_processor.write_file(path, update, expected_checksum=checksum)

    # Should succeed without checksum
    new_checksum = await markdown_processor.write_file(path, update)
    assert new_checksum != checksum
