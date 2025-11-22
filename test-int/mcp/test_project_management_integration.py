"""
Integration tests for project_management MCP tools.

Tests the complete project management workflow: MCP client -> MCP server -> FastAPI -> project service
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_list_projects_basic_operation(mcp_server, app, test_project):
    """Test basic list_projects operation showing available projects."""

    async with Client(mcp_server) as client:
        # List all available projects
        list_result = await client.call_tool(
            "list_memory_projects",
            {},
        )

        # Should return formatted project list
        assert len(list_result.content) == 1
        list_text = list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Should show available projects with new session guidance format
        assert "Available projects:" in list_text
        assert "test-project" in list_text  # Our test project
        # Check for new session guidance instead of CLI default
        assert "Next: Ask which project to use for this session." in list_text
        assert "Session reminder: Track the selected project" in list_text


@pytest.mark.asyncio
async def test_project_management_workflow(mcp_server, app, test_project):
    """Test basic project management workflow."""

    async with Client(mcp_server) as client:
        # List all projects
        list_result = await client.call_tool("list_memory_projects", {})
        assert "Available projects:" in list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "test-project" in list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_project_metadata_consistency(mcp_server, app, test_project):
    """Test that project management tools work correctly."""

    async with Client(mcp_server) as client:
        # Test basic project management tools

        # list_projects
        list_result = await client.call_tool("list_memory_projects", {})
        assert "Available projects:" in list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "test-project" in list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_create_project_basic_operation(mcp_server, app, test_project):
    """Test creating a new project with basic parameters."""

    async with Client(mcp_server) as client:
        # Create a new project
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": "test-new-project",
                "project_path": "/tmp/test-new-project",
            },
        )

        assert len(create_result.content) == 1
        create_text = create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Should show success message and project details
        assert "✓" in create_text  # Success indicator
        assert "test-new-project" in create_text
        assert "Project Details:" in create_text
        assert "Name: test-new-project" in create_text
        # Check path contains project name (platform-independent)
        assert "Path:" in create_text and "test-new-project" in create_text
        assert "Project is now available for use" in create_text

        # Verify project appears in project list
        list_result = await client.call_tool("list_memory_projects", {})
        list_text = list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "test-new-project" in list_text


@pytest.mark.asyncio
async def test_create_project_with_default_flag(mcp_server, app, test_project):
    """Test creating a project and setting it as default."""

    async with Client(mcp_server) as client:
        # Create a new project and set as default
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": "test-default-project",
                "project_path": "/tmp/test-default-project",
                "set_default": True,
            },
        )

        assert len(create_result.content) == 1
        create_text = create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Should show success and default flag
        assert "✓" in create_text
        assert "test-default-project" in create_text
        assert "Set as default project" in create_text

        # Verify the new project is listed
        list_after_create = await client.call_tool("list_memory_projects", {})
        assert "test-default-project" in list_after_create.content[0].text  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_create_project_duplicate_name(mcp_server, app, test_project):
    """Test creating a project with duplicate name shows error."""

    async with Client(mcp_server) as client:
        # First create a project
        await client.call_tool(
            "create_memory_project",
            {
                "project_name": "duplicate-test",
                "project_path": "/tmp/duplicate-test-1",
            },
        )

        # Try to create another project with same name
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "create_memory_project",
                {
                    "project_name": "duplicate-test",
                    "project_path": "/tmp/duplicate-test-2",
                },
            )

        # Should show error about duplicate name
        error_message = str(exc_info.value)
        assert "create_memory_project" in error_message
        assert (
            "duplicate-test" in error_message
            or "already exists" in error_message
            or "Invalid request" in error_message
        )


@pytest.mark.asyncio
async def test_delete_project_basic_operation(mcp_server, app, test_project):
    """Test deleting a project that exists."""

    async with Client(mcp_server) as client:
        # First create a project to delete
        await client.call_tool(
            "create_memory_project",
            {
                "project_name": "to-be-deleted",
                "project_path": "/tmp/to-be-deleted",
            },
        )

        # Verify it exists
        list_result = await client.call_tool("list_memory_projects", {})
        assert "to-be-deleted" in list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Delete the project
        delete_result = await client.call_tool(
            "delete_project",
            {
                "project_name": "to-be-deleted",
            },
        )

        assert len(delete_result.content) == 1
        delete_text = delete_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Should show success message
        assert "✓" in delete_text
        assert "to-be-deleted" in delete_text
        assert "removed successfully" in delete_text
        assert "Removed project details:" in delete_text
        assert "Name: to-be-deleted" in delete_text
        assert "Files remain on disk but project is no longer tracked" in delete_text

        # Verify project no longer appears in list
        list_result_after = await client.call_tool("list_memory_projects", {})
        assert "to-be-deleted" not in list_result_after.content[0].text  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_delete_project_not_found(mcp_server, app, test_project):
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
async def test_delete_current_project_protection(mcp_server, app, test_project):
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
async def test_project_lifecycle_workflow(mcp_server, app, test_project):
    """Test complete project lifecycle: create, switch, use, delete."""

    async with Client(mcp_server) as client:
        project_name = "lifecycle-test"
        project_path = "/tmp/lifecycle-test"

        # 1. Create new project
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": project_name,
                "project_path": project_path,
            },
        )
        assert "✓" in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert project_name in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # 2. Create content in the new project
        await client.call_tool(
            "write_note",
            {
                "project": project_name,
                "title": "Lifecycle Test Note",
                "folder": "test",
                "content": "# Lifecycle Test\\n\\nThis note tests the project lifecycle.\\n\\n- [test] Lifecycle testing",
                "tags": "lifecycle,test",
            },
        )

        # 3. Verify the project exists in the list
        list_with_content = await client.call_tool("list_memory_projects", {})
        assert project_name in list_with_content.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # 4. Verify we can still access the original test project
        test_list = await client.call_tool("list_memory_projects", {})
        assert "test-project" in test_list.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # 5. Delete the lifecycle test project
        delete_result = await client.call_tool(
            "delete_project",
            {
                "project_name": project_name,
            },
        )
        assert "✓" in delete_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert f"{project_name}" in delete_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "removed successfully" in delete_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # 6. Verify project is gone from list
        list_result = await client.call_tool("list_memory_projects", {})
        assert project_name not in list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_create_delete_project_edge_cases(mcp_server, app, test_project):
    """Test edge cases for create and delete project operations."""

    async with Client(mcp_server) as client:
        # Test with special characters and spaces in project name (should be handled gracefully)
        special_name = "test project with spaces & symbols!"

        # Create project with special characters
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": special_name,
                "project_path": "/tmp/test-project-with-special-chars",
            },
        )
        assert "✓" in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert special_name in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Verify it appears in list
        list_result = await client.call_tool("list_memory_projects", {})
        assert special_name in list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Delete it
        delete_result = await client.call_tool(
            "delete_project",
            {
                "project_name": special_name,
            },
        )
        assert "✓" in delete_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert special_name in delete_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Verify it's gone
        list_result_after = await client.call_tool("list_memory_projects", {})
        assert special_name not in list_result_after.content[0].text  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_case_insensitive_project_switching(mcp_server, app, test_project):
    """Test case-insensitive project switching with proper database lookup."""

    async with Client(mcp_server) as client:
        # Create a project with mixed case name
        project_name = "Personal-Project"
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": project_name,
                "project_path": f"/tmp/{project_name}",
            },
        )
        assert "✓" in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert project_name in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Verify project was created with canonical name
        list_result = await client.call_tool("list_memory_projects", {})
        assert project_name in list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Test with different case variations
        test_cases = [
            "personal-project",  # all lowercase
            "PERSONAL-PROJECT",  # all uppercase
            "Personal-project",  # mixed case 1
            "personal-Project",  # mixed case 2
        ]

        # Test that project operations work with case-insensitive input
        # (Project creation is case-preserving but operations can use different cases)

        # Test that we can reference the project with different cases in operations
        for test_input in test_cases:
            # Test write_note with case-insensitive project reference
            write_result = await client.call_tool(
                "write_note",
                {
                    "project": test_input,  # Use different case
                    "title": f"Case Test {test_input}",
                    "folder": "case-test",
                    "content": f"# Case Test\n\nTesting with {test_input}",
                },
            )
            assert len(write_result.content) == 1
            assert f"Case Test {test_input}".lower() in write_result.content[0].text.lower()  # pyright: ignore [reportAttributeAccessIssue]

        # Clean up
        await client.call_tool("delete_project", {"project_name": project_name})


@pytest.mark.asyncio
async def test_case_insensitive_project_operations(mcp_server, app, test_project):
    """Test that all project operations work correctly after case-insensitive switching."""

    async with Client(mcp_server) as client:
        # Create a project with capital letters
        project_name = "CamelCase-Project"
        create_result = await client.call_tool(
            "create_memory_project",
            {
                "project_name": project_name,
                "project_path": f"/tmp/{project_name}",
            },
        )
        assert "✓" in create_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Test that MCP operations work correctly with the project

        # 1. Create a note in the project
        write_result = await client.call_tool(
            "write_note",
            {
                "project": project_name,
                "title": "Case Test Note",
                "folder": "case-test",
                "content": "# Case Test Note\n\nTesting case-insensitive operations.\n\n- [test] Case insensitive switch\n- relates_to [[Another Note]]",
                "tags": "case,test",
            },
        )
        assert len(write_result.content) == 1
        assert "Case Test Note" in write_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # 2. Test search works in the project
        search_result = await client.call_tool(
            "search_notes",
            {"project": project_name, "query": "case insensitive"},
        )
        assert len(search_result.content) == 1
        assert "Case Test Note" in search_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # 3. Test read_note works
        read_result = await client.call_tool(
            "read_note",
            {"project": project_name, "identifier": "Case Test Note"},
        )
        assert len(read_result.content) == 1
        assert "Case Test Note" in read_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "case insensitive" in read_result.content[0].text.lower()  # pyright: ignore [reportAttributeAccessIssue]

        # Clean up
        await client.call_tool("delete_project", {"project_name": project_name})


@pytest.mark.asyncio
async def test_case_insensitive_error_handling(mcp_server, app, test_project):
    """Test error handling for case-insensitive project operations."""

    async with Client(mcp_server) as client:
        # Test non-existent project with various cases
        non_existent_cases = [
            "NonExistent",
            "non-existent",
            "NON-EXISTENT",
            "Non-Existent-Project",
        ]

        # Test that operations fail gracefully with non-existent projects
        for test_case in non_existent_cases:
            # Test that write_note fails with non-existent project
            with pytest.raises(Exception):
                await client.call_tool(
                    "write_note",
                    {
                        "project": test_case,
                        "title": "Test Note",
                        "folder": "test",
                        "content": "# Test\n\nTest content.",
                    },
                )


@pytest.mark.asyncio
async def test_case_preservation_in_project_list(mcp_server, app, test_project):
    """Test that project names preserve their original case in listings."""

    async with Client(mcp_server) as client:
        # Create projects with different casing patterns
        test_projects = [
            "lowercase-project",
            "UPPERCASE-PROJECT",
            "CamelCase-Project",
            "Mixed-CASE-project",
        ]

        # Create all test projects
        for project_name in test_projects:
            await client.call_tool(
                "create_memory_project",
                {
                    "project_name": project_name,
                    "project_path": f"/tmp/{project_name}",
                },
            )

        # List projects and verify each appears with its original case
        list_result = await client.call_tool("list_memory_projects", {})
        list_text = list_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        for project_name in test_projects:
            assert project_name in list_text, f"Project {project_name} not found in list"

        # Test each project with exact case (projects are case-sensitive)
        for project_name in test_projects:
            # Test write_note with exact project name
            write_result = await client.call_tool(
                "write_note",
                {
                    "project": project_name,  # Use exact project name
                    "title": f"Test Note {project_name}",
                    "folder": "test",
                    "content": f"# Test\n\nTesting {project_name}",
                },
            )
            assert len(write_result.content) == 1
            result_text = write_result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
            assert "successfully" in result_text.lower() or "created" in result_text.lower()

        # Clean up - delete test projects
        for project_name in test_projects:
            await client.call_tool("delete_project", {"project_name": project_name})


@pytest.mark.asyncio
async def test_nested_project_paths_rejected(mcp_server, app, test_project):
    """Test that creating nested project paths is rejected with clear error message."""

    async with Client(mcp_server) as client:
        # Create a parent project
        parent_name = "parent-project"
        parent_path = "/tmp/nested-test/parent"

        await client.call_tool(
            "create_memory_project",
            {
                "project_name": parent_name,
                "project_path": parent_path,
            },
        )

        # Try to create a child project nested under the parent
        child_name = "child-project"
        child_path = "/tmp/nested-test/parent/child"

        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "create_memory_project",
                {
                    "project_name": child_name,
                    "project_path": child_path,
                },
            )

        # Verify error message mentions nested paths
        error_message = str(exc_info.value)
        assert "nested" in error_message.lower()
        assert parent_name in error_message or parent_path in error_message

        # Clean up parent project
        await client.call_tool("delete_project", {"project_name": parent_name})
