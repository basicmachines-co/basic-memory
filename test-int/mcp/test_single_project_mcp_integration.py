"""
Integration tests for single project mode MCP functionality.

Tests the --project constraint feature that restricts MCP server to a single project,
covering project override behavior, project management tool restrictions, and
content tool functionality in constrained mode.
"""

import os
import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_project_constraint_override_content_tools(mcp_server, app, test_project):
    """Test that content tools use constrained project even when different project specified."""

    # Set up project constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    try:
        async with Client(mcp_server) as client:
            # Try to write to a different project - should be overridden
            result = await client.call_tool(
                "write_note",
                {
                    "project": "some-other-project",  # Should be ignored
                    "title": "Constraint Test Note",
                    "folder": "test",
                    "content": "# Constraint Test\n\nThis should go to the constrained project.",
                    "tags": "constraint,test",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should use the constrained project, not the requested one
            assert f"project: {test_project.name}" in response_text
            assert "# Created note" in response_text
            assert "file_path: test/Constraint Test Note.md" in response_text
            assert f"[Session: Using project '{test_project.name}']" in response_text

    finally:
        # Clean up environment variable
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_project_constraint_read_note_override(mcp_server, app, test_project):
    """Test that read_note also respects project constraint."""

    # Set up project constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    try:
        async with Client(mcp_server) as client:
            # First create a note
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Read Test Note",
                    "folder": "test",
                    "content": "# Read Test\n\nContent for reading test.",
                },
            )

            # Try to read from different project - should be overridden
            result = await client.call_tool(
                "read_note",
                {
                    "project": "wrong-project",  # Should be ignored
                    "identifier": "Read Test Note",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should find note in constrained project
            assert "# Read Test" in response_text
            # read_note returns the note content, not a summary with project info

    finally:
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_project_constraint_search_notes_override(mcp_server, app, test_project):
    """Test that search_notes respects project constraint."""

    # Set up project constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    try:
        async with Client(mcp_server) as client:
            # First create a searchable note
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": "Searchable Note",
                    "folder": "test",
                    "content": "# Searchable\n\nThis content has unique searchable terms.",
                },
            )

            # Try to search in different project - should be overridden
            result = await client.call_tool(
                "search_notes",
                {
                    "project": "different-project",  # Should be ignored
                    "query": "searchable terms",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should find results in constrained project
            assert "Searchable Note" in response_text
            # search_notes returns search results, check if it found the note

    finally:
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_list_projects_constrained_mode(mcp_server, app, test_project):
    """Test that list_memory_projects shows only constrained project."""

    # Set up project constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    try:
        async with Client(mcp_server) as client:
            result = await client.call_tool("list_memory_projects", {})

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should show constraint message
            assert "MCP server is constrained to a single project" in response_text

    finally:
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_create_project_disabled_in_constrained_mode(mcp_server, app, test_project):
    """Test that create_memory_project is disabled when server is constrained."""

    # Set up project constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    try:
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "create_memory_project",
                {"project_name": "new-project", "project_path": "/tmp/new-project"},
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should show error message
            assert "# Error" in response_text
            assert "Project creation disabled" in response_text
            assert f"constrained to project '{test_project.name}'" in response_text
            assert "Use the CLI to create projects:" in response_text
            assert 'basic-memory project add "new-project" "/tmp/new-project"' in response_text

    finally:
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_delete_project_disabled_in_constrained_mode(mcp_server, app, test_project):
    """Test that delete_project is disabled when server is constrained."""

    # Set up project constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    try:
        async with Client(mcp_server) as client:
            result = await client.call_tool("delete_project", {"project_name": "some-project"})

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should show error message
            assert "# Error" in response_text
            assert "Project deletion disabled" in response_text
            assert f"constrained to project '{test_project.name}'" in response_text
            assert "Use the CLI to delete projects:" in response_text
            assert 'basic-memory project remove "some-project"' in response_text

    finally:
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_normal_mode_without_constraint(mcp_server, app, test_project):
    """Test that tools work normally when no constraint is set."""

    # Ensure no constraint is set
    if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
        del os.environ["BASIC_MEMORY_MCP_PROJECT"]

    async with Client(mcp_server) as client:
        # Test write_note works with explicit project
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Normal Mode Note",
                "folder": "test",
                "content": "# Normal Mode\n\nThis should work normally.",
            },
        )

        assert len(result.content) == 1
        response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert f"project: {test_project.name}" in response_text
        assert "# Created note" in response_text

        # Test list_memory_projects works normally
        list_result = await client.call_tool("list_memory_projects", {})
        list_text = list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Should not show constraint message
        assert "MCP server is constrained to a single project" not in list_text


@pytest.mark.asyncio
async def test_constraint_with_multiple_content_tools(mcp_server, app, test_project):
    """Test that constraint works across multiple different content tools."""

    # Set up project constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    try:
        async with Client(mcp_server) as client:
            # Test write_note
            write_result = await client.call_tool(
                "write_note",
                {
                    "project": "wrong-project",
                    "title": "Multi Tool Test",
                    "folder": "test",
                    "content": "# Multi Tool Test\n\n- [note] Testing multiple tools",
                },
            )
            assert f"project: {test_project.name}" in write_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Test read_note
            read_result = await client.call_tool(
                "read_note", {"project": "another-wrong-project", "identifier": "Multi Tool Test"}
            )
            # Should successfully find the note (proving constraint worked)
            assert "# Multi Tool Test" in read_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Test search_notes
            search_result = await client.call_tool(
                "search_notes", {"project": "yet-another-wrong-project", "query": "multiple tools"}
            )
            # Should find results (proving constraint worked)
            assert "Multi Tool Test" in search_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

    finally:
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_constraint_environment_cleanup(mcp_server, app, test_project):
    """Test that removing constraint restores normal behavior."""

    # Set constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    async with Client(mcp_server) as client:
        # Verify constraint is active
        constrained_result = await client.call_tool("list_memory_projects", {})
        assert "MCP server is constrained to a single project" in constrained_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Remove constraint
        del os.environ["BASIC_MEMORY_MCP_PROJECT"]

        # Verify normal behavior is restored
        normal_result = await client.call_tool("list_memory_projects", {})
        normal_text = normal_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        assert "constrained to a single project" not in normal_text


@pytest.mark.asyncio
async def test_constraint_with_invalid_project_override(mcp_server, app, test_project):
    """Test constraint behavior when trying to override with invalid project names."""

    # Set up project constraint
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = test_project.name

    try:
        async with Client(mcp_server) as client:
            # Try various invalid project names - should all be overridden
            invalid_projects = [
                "non-existent-project",
                "",
                "project-with-special-chars!@#",
                "a" * 100,  # Very long name
            ]

            for i, invalid_project in enumerate(invalid_projects):
                result = await client.call_tool(
                    "write_note",
                    {
                        "project": invalid_project,
                        "title": f"Test Invalid {i} {invalid_project[:5]}",
                        "folder": "test",
                        "content": f"Testing with invalid project: {invalid_project}",
                    },
                )

                # Should still use the constrained project
                response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
                assert f"project: {test_project.name}" in response_text
                # Should create or update successfully
                assert "# Created note" in response_text or "# Updated note" in response_text

    finally:
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]
