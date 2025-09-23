"""Integration tests for Obsidian-compatible YAML formatting in write_note tool."""

import pytest

from basic_memory.mcp.tools import write_note


@pytest.mark.asyncio
async def test_write_note_tags_yaml_format(app, project_config, test_project):
    """Test that write_note creates files with proper YAML list format for tags."""
    # Create a note with tags using write_note
    result = await write_note.fn(
        project=test_project.name,
        title="YAML Format Test",
        folder="test",
        content="Testing YAML tag formatting",
        tags=["system", "overview", "reference"],
    )

    # Verify the note was created successfully
    assert "Created note" in result
    assert "file_path: test/YAML Format Test.md" in result

    # Read the file directly to check YAML formatting
    file_path = project_config.home / "test" / "YAML Format Test.md"
    content = file_path.read_text(encoding="utf-8")

    # Should use YAML list format
    assert "tags:" in content
    assert "- system" in content
    assert "- overview" in content
    assert "- reference" in content

    # Should NOT use JSON array format
    assert '["system"' not in content
    assert '"overview"' not in content
    assert '"reference"]' not in content


@pytest.mark.asyncio
async def test_write_note_stringified_json_tags(app, project_config, test_project):
    """Test that stringified JSON arrays are handled correctly."""
    # This simulates the issue where AI assistants pass tags as stringified JSON
    result = await write_note.fn(
        project=test_project.name,
        title="Stringified JSON Test",
        folder="test",
        content="Testing stringified JSON tag input",
        tags='["python", "testing", "json"]',  # Stringified JSON array
    )

    # Verify the note was created successfully
    assert "Created note" in result

    # Read the file to check formatting
    file_path = project_config.home / "test" / "Stringified JSON Test.md"
    content = file_path.read_text(encoding="utf-8")

    # Should properly parse the JSON and format as YAML list
    assert "tags:" in content
    assert "- python" in content
    assert "- testing" in content
    assert "- json" in content

    # Should NOT have the original stringified format issues
    assert '["python"' not in content
    assert '"testing"' not in content
    assert '"json"]' not in content


@pytest.mark.asyncio
async def test_write_note_single_tag_yaml_format(app, project_config, test_project):
    """Test that single tags are still formatted as YAML lists."""
    await write_note.fn(
        project=test_project.name,
        title="Single Tag Test",
        folder="test",
        content="Testing single tag formatting",
        tags=["solo-tag"],
    )

    file_path = project_config.home / "test" / "Single Tag Test.md"
    content = file_path.read_text(encoding="utf-8")

    # Single tag should still use list format
    assert "tags:" in content
    assert "- solo-tag" in content


@pytest.mark.asyncio
async def test_write_note_no_tags(app, project_config, test_project):
    """Test that notes without tags work normally."""
    await write_note.fn(
        project=test_project.name,
        title="No Tags Test",
        folder="test",
        content="Testing note without tags",
        tags=None,
    )

    file_path = project_config.home / "test" / "No Tags Test.md"
    content = file_path.read_text(encoding="utf-8")

    # Should not have tags field in frontmatter
    assert "tags:" not in content
    assert "title: No Tags Test" in content


@pytest.mark.asyncio
async def test_write_note_empty_tags_list(app, project_config, test_project):
    """Test that empty tag lists are handled properly."""
    await write_note.fn(
        project=test_project.name,
        title="Empty Tags Test",
        folder="test",
        content="Testing empty tag list",
        tags=[],
    )

    file_path = project_config.home / "test" / "Empty Tags Test.md"
    content = file_path.read_text(encoding="utf-8")

    # Should not add tags field to frontmatter for empty lists
    assert "tags:" not in content


@pytest.mark.asyncio
async def test_write_note_update_preserves_yaml_format(app, project_config, test_project):
    """Test that updating a note preserves the YAML list format."""
    # First, create the note
    await write_note.fn(
        project=test_project.name,
        title="Update Format Test",
        folder="test",
        content="Initial content",
        tags=["initial", "tag"],
    )

    # Then update it with new tags
    result = await write_note.fn(
        project=test_project.name,
        title="Update Format Test",
        folder="test",
        content="Updated content",
        tags=["updated", "new-tag", "format"],
    )

    # Should be an update, not a new creation
    assert "Updated note" in result

    # Check the file format
    file_path = project_config.home / "test" / "Update Format Test.md"
    content = file_path.read_text(encoding="utf-8")

    # Should have proper YAML formatting for updated tags
    assert "tags:" in content
    assert "- updated" in content
    assert "- new-tag" in content
    assert "- format" in content

    # Old tags should be gone
    assert "- initial" not in content
    assert "- tag" not in content

    # Content should be updated
    assert "Updated content" in content
    assert "Initial content" not in content


@pytest.mark.asyncio
async def test_complex_tags_yaml_format(app, project_config, test_project):
    """Test that complex tags with special characters format correctly."""
    await write_note.fn(
        project=test_project.name,
        title="Complex Tags Test",
        folder="test",
        content="Testing complex tag formats",
        tags=["python-3.9", "api_integration", "v2.0", "nested/category", "under_score"],
    )

    file_path = project_config.home / "test" / "Complex Tags Test.md"
    content = file_path.read_text(encoding="utf-8")

    # All complex tags should format correctly
    assert "- python-3.9" in content
    assert "- api_integration" in content
    assert "- v2.0" in content
    assert "- nested/category" in content
    assert "- under_score" in content
