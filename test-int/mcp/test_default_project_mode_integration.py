"""
Integration tests for default project mode functionality.

Tests the default_project_mode configuration that allows tools to automatically
use the default_project when no project parameter is specified, covering
parameter resolution hierarchy and mode-specific behavior.
"""

import os
from pathlib import Path

import pytest
from fastmcp import Client
from unittest.mock import patch

from basic_memory.config import ConfigManager, BasicMemoryConfig


@pytest.mark.asyncio
async def test_default_project_mode_enabled_write_note(mcp_server, app, test_project):
    """Test that write_note uses default project when default_project_mode=true and no project specified."""

    # Mock config with default_project_mode enabled
    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        default_project_mode=True,
        projects={test_project.name: test_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            # Call write_note without project parameter
            result = await client.call_tool(
                "write_note",
                {
                    "title": "Default Mode Test",
                    "folder": "test",
                    "content": "# Default Mode Test\n\nThis should use the default project automatically.",
                    "tags": "default,mode,test",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should use the default project
            assert f"project: {test_project.name}" in response_text
            assert "# Created note" in response_text
            assert "file_path: test/Default Mode Test.md" in response_text
            assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_default_project_mode_explicit_override(
    mcp_server, app, test_project, config_home, engine_factory
):
    """Test that explicit project parameter overrides default_project_mode."""

    # Create a second project for testing override
    engine, session_maker = engine_factory
    from basic_memory.repository.project_repository import ProjectRepository

    project_repository = ProjectRepository(session_maker)

    other_project = await project_repository.create(
        {
            "name": "other-project",
            "description": "Second project for testing",
            "path": str(config_home / "other-project"),
            "is_active": True,
            "is_default": False,
        }
    )

    # Mock config with default_project_mode enabled pointing to test_project
    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        default_project_mode=True,
        projects={test_project.name: test_project.path, other_project.name: other_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            # Call write_note with explicit project parameter (should override default)
            result = await client.call_tool(
                "write_note",
                {
                    "title": "Override Test",
                    "folder": "test",
                    "content": "# Override Test\n\nThis should go to the explicitly specified project.",
                    "project": other_project.name,  # Explicit override
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should use the explicitly specified project, not default
            assert f"project: {other_project.name}" in response_text
            assert "# Created note" in response_text
            assert f"[Session: Using project '{other_project.name}']" in response_text


@pytest.mark.asyncio
async def test_default_project_mode_disabled_requires_project(mcp_server, app, test_project):
    """Test that tools require project parameter when default_project_mode=false."""

    # Mock config with default_project_mode disabled
    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        default_project_mode=False,  # Disabled
        projects={test_project.name: test_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            # Call write_note without project parameter - should fail
            with pytest.raises(Exception) as exc_info:
                await client.call_tool(
                    "write_note",
                    {
                        "title": "Should Fail",
                        "folder": "test",
                        "content": "# Should Fail\n\nThis should fail because no project specified.",
                    },
                )

            # Should get an error about missing project
            error_message = str(exc_info.value)
            assert (
                "No project specified" in error_message
                or "project parameter" in error_message.lower()
            )


@pytest.mark.asyncio
async def test_cli_constraint_overrides_default_project_mode(
    mcp_server, app, test_project, config_home, engine_factory
):
    """Test that CLI --project constraint overrides default_project_mode."""

    # Create a different project for CLI constraint
    engine, session_maker = engine_factory
    from basic_memory.repository.project_repository import ProjectRepository

    project_repository = ProjectRepository(session_maker)

    other_project = await project_repository.create(
        {
            "name": "cli-project",
            "description": "Project for CLI constraint testing",
            "path": str(config_home / "cli-project"),
            "is_active": True,
            "is_default": False,
        }
    )

    # Set up CLI project constraint (highest priority)
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = other_project.name

    # Mock config with default_project_mode enabled pointing to test_project
    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        default_project_mode=True,
        projects={test_project.name: test_project.path, other_project.name: other_project.path},
    )

    try:
        with patch.object(ConfigManager, "config", mock_config):
            async with Client(mcp_server) as client:
                # Call write_note without project parameter
                result = await client.call_tool(
                    "write_note",
                    {
                        "title": "CLI Constraint Test",
                        "folder": "test",
                        "content": "# CLI Constraint Test\n\nThis should use CLI constrained project.",
                    },
                )

                assert len(result.content) == 1
                response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

                # Should use CLI constrained project, not default project
                assert f"project: {other_project.name}" in response_text
                assert "# Created note" in response_text
                assert f"[Session: Using project '{other_project.name}']" in response_text

    finally:
        # Clean up environment variable
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_default_project_mode_read_note(mcp_server, app, test_project):
    """Test that read_note works with default_project_mode."""

    # Mock config with default_project_mode enabled
    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        default_project_mode=True,
        projects={test_project.name: test_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            # First create a note
            await client.call_tool(
                "write_note",
                {
                    "title": "Read Test Note",
                    "folder": "test",
                    "content": "# Read Test Note\n\nThis note will be read back.",
                },
            )

            # Now read it back without specifying project
            result = await client.call_tool(
                "read_note",
                {
                    "identifier": "Read Test Note",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should successfully read the note
            assert "# Read Test Note" in response_text
            assert "This note will be read back." in response_text


@pytest.mark.asyncio
async def test_default_project_mode_edit_note(mcp_server, app, test_project):
    """Test that edit_note works with default_project_mode."""

    # Mock config with default_project_mode enabled
    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        default_project_mode=True,
        projects={test_project.name: test_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            # First create a note
            await client.call_tool(
                "write_note",
                {
                    "title": "Edit Test Note",
                    "folder": "test",
                    "content": "# Edit Test Note\n\nOriginal content.",
                },
            )

            # Now edit it without specifying project
            result = await client.call_tool(
                "edit_note",
                {
                    "identifier": "Edit Test Note",
                    "operation": "append",
                    "content": "\n\n## Added Content\n\nThis was added via edit_note.",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            # Should successfully edit the note
            assert "# Edited note" in response_text
            assert "operation: Added" in response_text


@pytest.mark.asyncio
async def test_project_resolution_hierarchy(
    mcp_server, app, test_project, config_home, engine_factory
):
    """Test the complete three-tier project resolution hierarchy."""

    # Create projects for testing
    engine, session_maker = engine_factory
    from basic_memory.repository.project_repository import ProjectRepository

    project_repository = ProjectRepository(session_maker)

    default_project = test_project
    cli_project = await project_repository.create(
        {
            "name": "cli-hierarchy-project",
            "description": "Project for CLI hierarchy testing",
            "path": str(config_home / "cli-hierarchy-project"),
            "is_active": True,
            "is_default": False,
        }
    )
    explicit_project = await project_repository.create(
        {
            "name": "explicit-hierarchy-project",
            "description": "Project for explicit hierarchy testing",
            "path": str(config_home / "explicit-hierarchy-project"),
            "is_active": True,
            "is_default": False,
        }
    )

    # Mock config with default_project_mode enabled
    mock_config = BasicMemoryConfig(
        default_project=default_project.name,
        default_project_mode=True,
        projects={
            default_project.name: Path(default_project.path).as_posix(),
            cli_project.name: Path(cli_project.path).as_posix(),
            explicit_project.name: Path(explicit_project.path).as_posix(),
        },
    )

    # Test 1: CLI constraint (highest priority)
    os.environ["BASIC_MEMORY_MCP_PROJECT"] = cli_project.name

    try:
        with patch.object(ConfigManager, "config", mock_config):
            async with Client(mcp_server) as client:
                result = await client.call_tool(
                    "write_note",
                    {
                        "title": "CLI Priority Test",
                        "folder": "test",
                        "content": "# CLI Priority Test",
                        "project": explicit_project.name,  # Should be ignored
                    },
                )
                response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
                assert f"project: {cli_project.name}" in response_text

    finally:
        del os.environ["BASIC_MEMORY_MCP_PROJECT"]

    # Test 2: Explicit project (medium priority)
    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "write_note",
                {
                    "title": "Explicit Priority Test",
                    "folder": "test",
                    "content": "# Explicit Priority Test",
                    "project": explicit_project.name,
                },
            )
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
            assert f"project: {explicit_project.name}" in response_text

    # Test 3: Default project (lowest priority)
    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "write_note",
                {
                    "title": "Default Priority Test",
                    "folder": "test",
                    "content": "# Default Priority Test",
                    # No project specified
                },
            )
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
            assert f"project: {default_project.name}" in response_text
