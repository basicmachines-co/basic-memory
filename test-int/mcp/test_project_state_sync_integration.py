"""Integration test for project state synchronization between MCP session and CLI config.

This test validates the fix for GitHub issue #148 where MCP session and CLI commands
had inconsistent project state, causing "Project not found" errors and edit failures.

The test simulates the exact workflow reported in the issue:
1. MCP server starts with a default project
2. Default project is changed via CLI/API
3. MCP tools should immediately use the new project (no restart needed)
4. All operations should work consistently in the new project context
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_project_state_sync_after_default_change(mcp_server, app, config_manager):
    """Test that MCP session stays in sync when default project is changed."""

    async with Client(mcp_server) as client:
        # Step 1: Verify initial state - MCP should show test-project as current
        initial_result = await client.call_tool("get_current_project", {})
        assert len(initial_result.content) == 1
        assert "Current project: test-project" in initial_result.content[0].text

        # Step 2: Create a second project that we can switch to
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": "minerva",
                "project_path": "/tmp/minerva-test-project",
                "set_default": False,  # Don't set as default yet
            },
        )
        assert len(create_result.content) == 1
        assert "✓" in create_result.content[0].text
        assert "minerva" in create_result.content[0].text

        # Step 3: Change default project to minerva via set_default_project tool
        # This simulates the CLI command `basic-memory project default minerva`
        set_default_result = await client.call_tool(
            "set_default_project", {"project_name": "minerva"}
        )
        assert len(set_default_result.content) == 1
        assert "✓" in set_default_result.content[0].text
        assert "minerva" in set_default_result.content[0].text

        # Step 4: Verify MCP session immediately reflects the change (no restart needed)
        # This tests the fix - session.refresh_from_config() should have been called
        updated_result = await client.call_tool("get_current_project", {})
        assert len(updated_result.content) == 1

        # The fix should ensure these are consistent now:
        updated_text = updated_result.content[0].text
        assert "Current project: minerva" in updated_text

        # Step 5: Verify config manager also shows the new default
        assert config_manager.default_project == "minerva"

        # Step 6: Test that note operations work in the new project context
        # This validates that the identifier resolution works correctly
        write_result = await client.call_tool(
            "write_note",
            {
                "title": "Test Consistency Note",
                "folder": "test",
                "content": "# Test Note\n\nThis note tests project state consistency.\n\n- [test] Project state sync working",
                "tags": "test,consistency",
            },
        )
        assert len(write_result.content) == 1
        assert "Test Consistency Note" in write_result.content[0].text

        # Step 7: Test that we can read the note we just created
        read_result = await client.call_tool("read_note", {"identifier": "Test Consistency Note"})
        assert len(read_result.content) == 1
        assert "Test Consistency Note" in read_result.content[0].text
        assert "project state sync working" in read_result.content[0].text.lower()

        # Step 8: Test that edit operations work (this was failing in the original issue)
        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "Test Consistency Note",
                "operation": "append",
                "content": "\n\n## Update\n\nEdit operation successful after project switch!",
            },
        )
        assert len(edit_result.content) == 1
        assert (
            "added" in edit_result.content[0].text.lower()
            and "lines" in edit_result.content[0].text.lower()
        )

        # Step 9: Verify the edit was applied
        final_read_result = await client.call_tool(
            "read_note", {"identifier": "Test Consistency Note"}
        )
        assert len(final_read_result.content) == 1
        final_content = final_read_result.content[0].text
        assert "Edit operation successful" in final_content

        # Clean up - switch back to test-project
        await client.call_tool("switch_project", {"project_name": "test-project"})


@pytest.mark.asyncio
async def test_multiple_project_switches_maintain_consistency(mcp_server, app, config_manager):
    """Test that multiple project switches maintain consistent state."""

    async with Client(mcp_server) as client:
        # Create multiple test projects
        for project_name in ["project-a", "project-b", "project-c"]:
            await client.call_tool(
                "create_memory_project",
                {
                    "project_name": project_name,
                    "project_path": f"/tmp/{project_name}",
                    "set_default": False,
                },
            )

        # Test switching between projects multiple times
        for project_name in ["project-a", "project-b", "project-c", "test-project"]:
            # Set as default
            set_result = await client.call_tool(
                "set_default_project", {"project_name": project_name}
            )
            assert "✓" in set_result.content[0].text

            # Verify MCP session immediately reflects the change
            current_result = await client.call_tool("get_current_project", {})
            assert f"Current project: {project_name}" in current_result.content[0].text

            # Verify config is also updated
            assert config_manager.default_project == project_name

            # Test that operations work in this project
            note_title = f"Note in {project_name}"
            write_result = await client.call_tool(
                "write_note",
                {
                    "title": note_title,
                    "folder": "test",
                    "content": f"# {note_title}\n\nTesting operations in {project_name}.",
                    "tags": "test",
                },
            )
            assert note_title in write_result.content[0].text

        # Clean up - switch back to test-project
        await client.call_tool("set_default_project", {"project_name": "test-project"})


@pytest.mark.asyncio
async def test_session_handles_nonexistent_project_gracefully(mcp_server, app):
    """Test that session handles attempts to switch to nonexistent projects gracefully."""

    async with Client(mcp_server) as client:
        # Try to switch to a project that doesn't exist
        switch_result = await client.call_tool(
            "switch_project", {"project_name": "nonexistent-project"}
        )
        assert len(switch_result.content) == 1
        result_text = switch_result.content[0].text

        # Should show an error message
        assert "Error:" in result_text
        assert "not found" in result_text.lower()
        assert "Available projects:" in result_text
        assert "test-project" in result_text  # Should list available projects

        # Verify the session stays on the original project
        current_result = await client.call_tool("get_current_project", {})
        assert "Current project: test-project" in current_result.content[0].text
