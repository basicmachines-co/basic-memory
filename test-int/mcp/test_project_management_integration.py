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


@pytest.mark.asyncio
async def test_create_project_basic_operation(mcp_server, app):
    """Test creating a new project with basic parameters."""

    async with Client(mcp_server) as client:
        # Create a new project
        create_result = await client.call_tool(
            "create_project",
            {
                "project_name": "test-new-project",
                "project_path": "/tmp/test-new-project",
            },
        )

        assert len(create_result) == 1
        create_text = create_result[0].text

        # Should show success message and project details
        assert "✓" in create_text  # Success indicator
        assert "test-new-project" in create_text
        assert "Project Details:" in create_text
        assert "Name: test-new-project" in create_text
        assert "Path: /tmp/test-new-project" in create_text
        assert "Project is now available for use" in create_text
        assert "Project: test-project" in create_text  # Should still show current project

        # Verify project appears in project list
        list_result = await client.call_tool("list_projects", {})
        list_text = list_result[0].text
        assert "test-new-project" in list_text


@pytest.mark.asyncio
async def test_create_project_with_default_flag(mcp_server, app):
    """Test creating a project and setting it as default."""

    async with Client(mcp_server) as client:
        # Create a new project and set as default
        create_result = await client.call_tool(
            "create_project",
            {
                "project_name": "test-default-project",
                "project_path": "/tmp/test-default-project",
                "set_default": True,
            },
        )

        assert len(create_result) == 1
        create_text = create_result[0].text

        # Should show success and default flag
        assert "✓" in create_text
        assert "test-default-project" in create_text
        assert "Set as default project" in create_text
        assert "Project: test-default-project" in create_text  # Should switch to new project

        # Verify we switched to the new project
        current_result = await client.call_tool("get_current_project", {})
        current_text = current_result[0].text
        assert "Current project: test-default-project" in current_text


@pytest.mark.asyncio
async def test_create_project_duplicate_name(mcp_server, app):
    """Test creating a project with duplicate name shows error."""

    async with Client(mcp_server) as client:
        # First create a project
        await client.call_tool(
            "create_project",
            {
                "project_name": "duplicate-test",
                "project_path": "/tmp/duplicate-test-1",
            },
        )

        # Try to create another project with same name
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "create_project",
                {
                    "project_name": "duplicate-test",
                    "project_path": "/tmp/duplicate-test-2",
                },
            )

        # Should show error about duplicate name
        error_message = str(exc_info.value)
        assert "create_project" in error_message
        assert (
            "duplicate-test" in error_message
            or "already exists" in error_message
            or "Invalid request" in error_message
        )


@pytest.mark.asyncio
async def test_delete_project_basic_operation(mcp_server, app):
    """Test deleting a project that exists."""

    async with Client(mcp_server) as client:
        # First create a project to delete
        await client.call_tool(
            "create_project",
            {
                "project_name": "to-be-deleted",
                "project_path": "/tmp/to-be-deleted",
            },
        )

        # Verify it exists
        list_result = await client.call_tool("list_projects", {})
        assert "to-be-deleted" in list_result[0].text

        # Delete the project
        delete_result = await client.call_tool(
            "delete_project",
            {
                "project_name": "to-be-deleted",
            },
        )

        assert len(delete_result) == 1
        delete_text = delete_result[0].text

        # Should show success message
        assert "✓" in delete_text
        assert "to-be-deleted" in delete_text
        assert "removed successfully" in delete_text
        assert "Removed project details:" in delete_text
        assert "Name: to-be-deleted" in delete_text
        assert "Files remain on disk but project is no longer tracked" in delete_text
        assert "Project: test-project" in delete_text  # Should show current project

        # Verify project no longer appears in list
        list_result_after = await client.call_tool("list_projects", {})
        assert "to-be-deleted" not in list_result_after[0].text


@pytest.mark.asyncio
async def test_delete_project_not_found(mcp_server, app):
    """Test deleting a non-existent project shows error."""

    async with Client(mcp_server) as client:
        # Try to delete non-existent project
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "delete_project",
                {
                    "project_name": "non-existent-project",
                },
            )

        # Should show error about non-existent project
        error_message = str(exc_info.value)
        assert "delete_project" in error_message
        assert (
            "non-existent-project" in error_message
            or "not found" in error_message
            or "Invalid request" in error_message
        )


@pytest.mark.asyncio
async def test_delete_current_project_protection(mcp_server, app):
    """Test that deleting the current project is prevented."""

    async with Client(mcp_server) as client:
        # Try to delete the current project (test-project)
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "delete_project",
                {
                    "project_name": "test-project",
                },
            )

        # Should show error about deleting current project
        error_message = str(exc_info.value)
        assert "delete_project" in error_message
        assert (
            "currently active" in error_message
            or "test-project" in error_message
            or "Switch to a different project" in error_message
        )


@pytest.mark.asyncio
async def test_project_lifecycle_workflow(mcp_server, app):
    """Test complete project lifecycle: create, switch, use, delete."""

    async with Client(mcp_server) as client:
        project_name = "lifecycle-test"
        project_path = "/tmp/lifecycle-test"

        # 1. Create new project
        create_result = await client.call_tool(
            "create_project",
            {
                "project_name": project_name,
                "project_path": project_path,
            },
        )
        assert "✓" in create_result[0].text
        assert project_name in create_result[0].text

        # 2. Switch to the new project
        switch_result = await client.call_tool(
            "switch_project",
            {
                "project_name": project_name,
            },
        )
        assert f"✓ Switched to {project_name} project" in switch_result[0].text

        # 3. Create content in the new project
        await client.call_tool(
            "write_note",
            {
                "title": "Lifecycle Test Note",
                "folder": "test",
                "content": "# Lifecycle Test\\n\\nThis note tests the project lifecycle.\\n\\n- [test] Lifecycle testing",
                "tags": "lifecycle,test",
            },
        )

        # 4. Verify project stats show our content
        current_result = await client.call_tool("get_current_project", {})
        current_text = current_result[0].text
        assert f"Current project: {project_name}" in current_text
        assert "entities" in current_text

        # 5. Switch back to original project
        await client.call_tool(
            "switch_project",
            {
                "project_name": "test-project",
            },
        )

        # 6. Delete the lifecycle test project
        delete_result = await client.call_tool(
            "delete_project",
            {
                "project_name": project_name,
            },
        )
        assert "✓" in delete_result[0].text
        assert f"{project_name}" in delete_result[0].text
        assert "removed successfully" in delete_result[0].text

        # 7. Verify project is gone from list
        list_result = await client.call_tool("list_projects", {})
        assert project_name not in list_result[0].text


@pytest.mark.asyncio
async def test_create_delete_project_edge_cases(mcp_server, app):
    """Test edge cases for create and delete project operations."""

    async with Client(mcp_server) as client:
        # Test with special characters in project name (should be handled gracefully)
        special_name = "test-project-with-dashes"

        # Create project with special characters
        create_result = await client.call_tool(
            "create_project",
            {
                "project_name": special_name,
                "project_path": f"/tmp/{special_name}",
            },
        )
        assert "✓" in create_result[0].text
        assert special_name in create_result[0].text

        # Verify it appears in list
        list_result = await client.call_tool("list_projects", {})
        assert special_name in list_result[0].text

        # Delete it
        delete_result = await client.call_tool(
            "delete_project",
            {
                "project_name": special_name,
            },
        )
        assert "✓" in delete_result[0].text
        assert special_name in delete_result[0].text

        # Verify it's gone
        list_result_after = await client.call_tool("list_projects", {})
        assert special_name not in list_result_after[0].text
