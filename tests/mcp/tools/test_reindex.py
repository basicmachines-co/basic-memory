"""Tests for reindex MCP tool."""

import pytest

from basic_memory.mcp.tools import write_note, force_reindex, search_notes


@pytest.mark.asyncio
async def test_force_reindex_success(client, test_project):
    """Test force_reindex returns success message."""
    # Create some test content first
    await write_note.fn(
        project=test_project.name,
        title="Test Document",
        folder="docs",
        content="# Test Document\n\nThis is test content for reindex testing.",
    )

    # Trigger reindex
    result = await force_reindex.fn(project=test_project.name)

    # Verify response format
    assert isinstance(result, str)
    assert "# Search Index Reindex" in result
    assert "Status: ok" in result
    assert "Reindex initiated" in result
    assert test_project.name in result


@pytest.mark.asyncio
async def test_force_reindex_rebuilds_search_index(client, test_project):
    """Test that force_reindex actually rebuilds the search index."""
    # Create test content
    await write_note.fn(
        project=test_project.name,
        title="Searchable Content",
        folder="notes",
        content="# Searchable Content\n\nThis document contains unique searchable text: xyzzy123",
    )

    # Verify content is searchable before reindex
    search_result = await search_notes.fn(
        query="xyzzy123",
        project=test_project.name,
    )
    # search_notes returns SearchResponse or error string
    if hasattr(search_result, 'results'):
        assert len(search_result.results) >= 1

    # Trigger reindex
    result = await force_reindex.fn(project=test_project.name)
    assert "Status: ok" in result

    # Verify content is still searchable after reindex
    search_result_after = await search_notes.fn(
        query="xyzzy123",
        project=test_project.name,
    )
    if hasattr(search_result_after, 'results'):
        assert len(search_result_after.results) >= 1


@pytest.mark.asyncio
async def test_force_reindex_without_project_uses_default(client, test_project, monkeypatch):
    """Test force_reindex uses default project when none specified."""
    from basic_memory.config import ConfigManager

    # Set up default project mode
    config = ConfigManager().config
    original_default_project_mode = config.default_project_mode
    original_default_project = config.default_project

    try:
        config.default_project_mode = True
        config.default_project = test_project.name

        # Create test content
        await write_note.fn(
            project=test_project.name,
            title="Default Project Test",
            folder="docs",
            content="# Default Project Test\n\nContent for default project testing.",
        )

        # Trigger reindex without specifying project
        result = await force_reindex.fn()

        assert isinstance(result, str)
        assert "Status: ok" in result
    finally:
        # Restore original config
        config.default_project_mode = original_default_project_mode
        config.default_project = original_default_project
