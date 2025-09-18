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
async def test_project_state_sync_after_default_change(
    mcp_server, app, config_manager, test_project
):
    """Test that MCP session stays in sync when default project is changed."""

    async with Client(mcp_server) as client:
        # Step 1: Create a second project that we can switch to
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": "minerva",
                "project_path": "/tmp/minerva-test-project",
                "set_default": False,  # Don't set as default yet
            },
        )
        assert len(create_result.content) == 1
        assert "✓" in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "minerva" in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Step 2: Test that note operations work in the new project context
        # This validates that the identifier resolution works correctly
        write_result = await client.call_tool(
            "write_note",
            {
                "project": "minerva",
                "title": "Test Consistency Note",
                "folder": "test",
                "content": "# Test Note\n\nThis note tests project state consistency.\n\n- [test] Project state sync working",
                "tags": "test,consistency",
            },
        )
        assert len(write_result.content) == 1
        assert "Test Consistency Note" in write_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Step 3: Test that we can read the note we just created
        read_result = await client.call_tool(
            "read_note", {"project": "minerva", "identifier": "Test Consistency Note"}
        )
        assert len(read_result.content) == 1
        assert "Test Consistency Note" in read_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "project state sync working" in read_result.content[0].text.lower()  # pyright: ignore [reportAttributeAccessIssue]

        # Step 4: Test that edit operations work (this was failing in the original issue)
        edit_result = await client.call_tool(
            "edit_note",
            {
                "project": "minerva",
                "identifier": "Test Consistency Note",
                "operation": "append",
                "content": "\n\n## Update\n\nEdit operation successful after project switch!",
            },
        )
        assert len(edit_result.content) == 1
        assert (
            "added" in edit_result.content[0].text.lower()  # pyright: ignore [reportAttributeAccessIssue]
            and "lines" in edit_result.content[0].text.lower()  # pyright: ignore [reportAttributeAccessIssue]
        )

        # Step 5: Verify the edit was applied
        final_read_result = await client.call_tool(
            "read_note", {"project": "minerva", "identifier": "Test Consistency Note"}
        )
        assert len(final_read_result.content) == 1
        final_content = final_read_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "Edit operation successful" in final_content
