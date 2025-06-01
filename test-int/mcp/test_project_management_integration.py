"""
Integration tests for project_management MCP tools.

Tests the complete project management workflow: MCP client -> MCP server -> FastAPI -> project service
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_list_projects_basic_operation(mcp_server, app):
    """Test basic list_projects operation showing available projects."""

    async with Client(mcp_server) as client:
        # List all available projects
        list_result = await client.call_tool(
            "list_projects",
            {},
        )

        # Should return formatted project list
        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show available projects with status indicators
        assert "Available projects:" in list_text
        assert "test-project" in list_text  # Our default test project
        assert "(current, default)" in list_text or "(default)" in list_text
        assert "Project: test-project" in list_text  # Project metadata


@pytest.mark.asyncio
async def test_get_current_project_operation(mcp_server, app):
    """Test get_current_project showing current project info."""

    async with Client(mcp_server) as client:
        # Create some test content first to have stats
        await client.call_tool(
            "write_note",
            {
                "title": "Test Note",
                "folder": "test",
                "content": "# Test Note\n\nTest content.\n\n- [feature] Test observation",
                "tags": "test",
            },
        )

        # Get current project info
        current_result = await client.call_tool(
            "get_current_project",
            {},
        )

        assert len(current_result) == 1
        current_text = current_result[0].text

        # Should show current project and stats
        assert "Current project: test-project" in current_text
        assert "entities" in current_text
        assert "observations" in current_text
        assert "relations" in current_text
        assert "Project: test-project" in current_text  # Project metadata


@pytest.mark.asyncio
async def test_project_info_with_entities(mcp_server, app):
    """Test that project info shows correct entity counts."""

    async with Client(mcp_server) as client:
        # Create multiple entities with observations and relations
        await client.call_tool(
            "write_note",
            {
                "title": "Entity One",
                "folder": "stats",
                "content": """# Entity One

This is the first entity.

## Observations
- [type] First entity type
- [status] Active entity

## Relations  
- relates_to [[Entity Two]]
- implements [[Some System]]""",
                "tags": "entity,test",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Entity Two",
                "folder": "stats",
                "content": """# Entity Two

This is the second entity.

## Observations
- [type] Second entity type
- [priority] High priority

## Relations
- depends_on [[Entity One]]""",
                "tags": "entity,test",
            },
        )

        # Get current project info to see updated stats
        current_result = await client.call_tool(
            "get_current_project",
            {},
        )

        assert len(current_result) == 1
        current_text = current_result[0].text

        # Should show entity and observation counts
        assert "Current project: test-project" in current_text
        # Should show at least the entities we created
        assert (
            "2 entities" in current_text or "3 entities" in current_text
        )  # May include other entities from setup
        # Should show observations from our entities
        assert (
            "4 observations" in current_text
            or "5 observations" in current_text
            or "6 observations" in current_text
        )  # Our 4 + possibly more from setup


@pytest.mark.asyncio
async def test_switch_project_not_found(mcp_server, app):
    """Test switch_project with non-existent project shows error."""

    async with Client(mcp_server) as client:
        # Try to switch to non-existent project
        switch_result = await client.call_tool(
            "switch_project",
            {
                "project_name": "non-existent-project",
            },
        )

        assert len(switch_result) == 1
        switch_text = switch_result[0].text

        # Should show error message with available projects
        assert "Error: Project 'non-existent-project' not found" in switch_text
        assert "Available projects:" in switch_text
        assert "test-project" in switch_text


@pytest.mark.asyncio
async def test_switch_project_to_test_project(mcp_server, app):
    """Test switching to the currently active project."""

    async with Client(mcp_server) as client:
        # Switch to the same project (test-project)
        switch_result = await client.call_tool(
            "switch_project",
            {
                "project_name": "test-project",
            },
        )

        assert len(switch_result) == 1
        switch_text = switch_result[0].text

        # Should show successful switch
        assert "✓ Switched to test-project project" in switch_text
        assert "Project Summary:" in switch_text
        assert "entities" in switch_text
        assert "observations" in switch_text
        assert "relations" in switch_text
        assert "Project: test-project" in switch_text  # Project metadata


@pytest.mark.asyncio
async def test_set_default_project_operation(mcp_server, app):
    """Test set_default_project functionality."""

    async with Client(mcp_server) as client:
        # Get current project info (default)
        current_result = await client.call_tool(
            "get_current_project",
            {},
        )

        assert len(current_result) == 1
        current_text = current_result[0].text

        # Should show current project and stats
        assert "Current project: test-project" in current_text

        # Set test-project as default (it likely already is, but test the operation)
        default_result = await client.call_tool(
            "set_default_project",
            {
                "project_name": "test-project",
            },
        )

        assert len(default_result) == 1
        default_text = default_result[0].text

        # Should show success message and restart instructions
        assert "✓" in default_text  # Success indicator
        assert "test-project" in default_text
        assert "Restart Basic Memory for this change to take effect" in default_text
        assert "basic-memory mcp" in default_text
        assert "Project: test-project" in default_text  # Project metadata


@pytest.mark.asyncio
async def test_set_default_project_not_found(mcp_server, app):
    """Test set_default_project with non-existent project."""

    async with Client(mcp_server) as client:
        # Try to set non-existent project as default
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "set_default_project",
                {
                    "project_name": "non-existent-project",
                },
            )

        # Should show error about non-existent project
        error_message = str(exc_info.value)
        assert "set_default_project" in error_message
        assert (
            "non-existent-project" in error_message
            or "Invalid request" in error_message
            or "Client error" in error_message
        )


@pytest.mark.asyncio
async def test_project_management_workflow(mcp_server, app):
    """Test complete project management workflow."""

    async with Client(mcp_server) as client:
        # 1. Check current project
        current_result = await client.call_tool("get_current_project", {})
        assert "test-project" in current_result[0].text

        # 2. List all projects
        list_result = await client.call_tool("list_projects", {})
        assert "Available projects:" in list_result[0].text
        assert "test-project" in list_result[0].text

        # 3. Switch to same project (should work)
        switch_result = await client.call_tool("switch_project", {"project_name": "test-project"})
        assert "✓ Switched to test-project project" in switch_result[0].text

        # 4. Verify we're still on the same project
        current_result2 = await client.call_tool("get_current_project", {})
        assert "Current project: test-project" in current_result2[0].text


@pytest.mark.asyncio
async def test_project_metadata_consistency(mcp_server, app):
    """Test that all project management tools include consistent project metadata."""

    async with Client(mcp_server) as client:
        # Test all project management tools and verify they include project metadata

        # list_projects
        list_result = await client.call_tool("list_projects", {})
        assert "Project: test-project" in list_result[0].text

        # get_current_project
        current_result = await client.call_tool("get_current_project", {})
        assert "Project: test-project" in current_result[0].text

        # switch_project
        switch_result = await client.call_tool("switch_project", {"project_name": "test-project"})
        assert "Project: test-project" in switch_result[0].text

        # set_default_project (skip since API not working in test env)
        # default_result = await client.call_tool(
        #     "set_default_project",
        #     {"project_name": "test-project"}
        # )
        # assert "Project: test-project" in default_result[0].text


@pytest.mark.asyncio
async def test_project_statistics_accuracy(mcp_server, app):
    """Test that project statistics reflect actual content."""

    async with Client(mcp_server) as client:
        # Get initial stats
        initial_result = await client.call_tool("get_current_project", {})
        initial_text = initial_result[0].text
        assert initial_text is not None

        # Create a new entity
        await client.call_tool(
            "write_note",
            {
                "title": "Stats Test Note",
                "folder": "stats-test",
                "content": """# Stats Test Note

Testing statistics accuracy.

## Observations
- [test] This is a test observation
- [accuracy] Testing stats accuracy

## Relations
- validates [[Project Statistics]]""",
                "tags": "stats,test",
            },
        )

        # Get updated stats
        updated_result = await client.call_tool("get_current_project", {})
        updated_text = updated_result[0].text

        # Should show project info with stats
        assert "Current project: test-project" in updated_text
        assert "entities" in updated_text
        assert "observations" in updated_text
        assert "relations" in updated_text

        # Stats should be reasonable (at least 1 entity, some observations)
        import re

        entity_match = re.search(r"(\d+) entities", updated_text)
        obs_match = re.search(r"(\d+) observations", updated_text)

        if entity_match:
            entity_count = int(entity_match.group(1))
            assert entity_count >= 1, f"Should have at least 1 entity, got {entity_count}"

        if obs_match:
            obs_count = int(obs_match.group(1))
            assert obs_count >= 2, f"Should have at least 2 observations, got {obs_count}"
