"""
Integration tests for explicit MCP scope with configured project defaults.

Defaults and server constraints still route scoped requests. They cannot supply
a missing required tool argument.
"""

import os
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from unittest.mock import patch

from basic_memory import db
from basic_memory.config import ConfigManager, BasicMemoryConfig
from basic_memory.repository.project_repository import ProjectRepository


@pytest.mark.asyncio
async def test_explicit_write_in_default_project(mcp_server, app, test_project):
    """Explicit scope selects the configured default project."""

    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        projects={test_project.name: test_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            with pytest.raises(ToolError, match="Missing required keyword only argument"):
                await client.call_tool(
                    "bm_write",
                    {"title": "Unscoped write", "content": "Must select the default explicitly."},
                )
            result = await client.call_tool(
                "bm_write",
                {
                    "project": test_project.name,
                    "title": "Default Mode Test",
                    "directory": "test",
                    "content": "# Default Mode Test\n\nThis explicitly selects the configured default project.",
                    "tags": "default,mode,test",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            assert f"permalink: {test_project.name}/" in response_text
            assert "Created " in response_text
            assert "Created test/Default Mode Test.md" in response_text


@pytest.mark.asyncio
async def test_explicit_project_overrides_default(
    mcp_server, app, test_project, config_home, engine_factory
):
    """Test that explicit project parameter overrides default_project."""

    engine, session_maker = engine_factory

    project_repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        other_project = await project_repository.create(
            session,
            {
                "name": "other-project",
                "description": "Second project for testing",
                "path": str(config_home / "other-project"),
                "is_active": True,
                "is_default": False,
            },
        )

    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        projects={test_project.name: test_project.path, other_project.name: other_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "bm_write",
                {
                    "title": "Override Test",
                    "directory": "test",
                    "content": "# Override Test\n\nThis should go to the explicitly specified project.",
                    "project": other_project.name,
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            assert f"permalink: {other_project.name}/" in response_text
            assert "Created " in response_text


@pytest.mark.asyncio
async def test_explicit_write_without_config_default(mcp_server, app, test_project):
    """Explicit scope works without a configured default."""

    mock_config = BasicMemoryConfig(
        default_project=None,  # No config default
        projects={test_project.name: test_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "bm_write",
                {
                    "project": test_project.name,
                    "title": "DB Fallback Test",
                    "directory": "test",
                    "content": "# DB Fallback Test\n\nSelect the database default project explicitly.",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            assert f"permalink: {test_project.name}/" in response_text
            assert "Created " in response_text


@pytest.mark.asyncio
async def test_cli_constraint_overrides_default_project(
    mcp_server, app, test_project, config_home, engine_factory
):
    """Test that CLI --project constraint overrides default_project."""

    engine, session_maker = engine_factory

    project_repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        other_project = await project_repository.create(
            session,
            {
                "name": "cli-project",
                "description": "Project for CLI constraint testing",
                "path": str(config_home / "cli-project"),
                "is_active": True,
                "is_default": False,
            },
        )

    os.environ["BASIC_MEMORY_MCP_PROJECT"] = other_project.name

    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        projects={test_project.name: test_project.path, other_project.name: other_project.path},
    )

    try:
        with patch.object(ConfigManager, "config", mock_config):
            async with Client(mcp_server) as client:
                result = await client.call_tool(
                    "bm_write",
                    {
                        "project": other_project.name,
                        "title": "CLI Constraint Test",
                        "directory": "test",
                        "content": "# CLI Constraint Test\n\nThis should use CLI constrained project.",
                    },
                )

                assert len(result.content) == 1
                response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

                assert f"permalink: {other_project.name}/" in response_text
                assert "Created " in response_text

    finally:
        if "BASIC_MEMORY_MCP_PROJECT" in os.environ:
            del os.environ["BASIC_MEMORY_MCP_PROJECT"]


@pytest.mark.asyncio
async def test_explicit_read_in_default_project(mcp_server, app, test_project):
    """Explicit scope supports read-after-write in the default project."""

    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        projects={test_project.name: test_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            await client.call_tool(
                "bm_write",
                {
                    "project": test_project.name,
                    "title": "Read Test Note",
                    "directory": "test",
                    "content": "# Read Test Note\n\nThis note will be read back.",
                },
            )

            result = await client.call_tool(
                "bm_read",
                {
                    "identifier": "Read Test Note",
                    "include_frontmatter": False,
                    "project": test_project.name,
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            assert "# Read Test Note" in response_text
            assert "This note will be read back." in response_text


@pytest.mark.asyncio
async def test_explicit_edit_in_default_project(mcp_server, app, test_project):
    """Explicit scope supports edits in the default project."""

    mock_config = BasicMemoryConfig(
        default_project=test_project.name,
        projects={test_project.name: test_project.path},
    )

    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            await client.call_tool(
                "bm_write",
                {
                    "project": test_project.name,
                    "title": "Edit Test Note",
                    "directory": "test",
                    "content": "# Edit Test Note\n\nOriginal content.",
                },
            )

            result = await client.call_tool(
                "bm_edit",
                {
                    "project": test_project.name,
                    "identifier": "Edit Test Note",
                    "command": "append",
                    "content": "\n\n## Added Content\n\nThis was added via edit_note.",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

            assert "Edited " in response_text
            assert "(append)" in response_text


@pytest.mark.asyncio
async def test_project_resolution_hierarchy(
    mcp_server, app, test_project, config_home, engine_factory
):
    """Explicit scope is required while server constraints retain routing priority."""

    engine, session_maker = engine_factory

    project_repository = ProjectRepository()

    default_project = test_project
    async with db.scoped_session(session_maker) as session:
        cli_project = await project_repository.create(
            session,
            {
                "name": "cli-hierarchy-project",
                "description": "Project for CLI hierarchy testing",
                "path": str(config_home / "cli-hierarchy-project"),
                "is_active": True,
                "is_default": False,
            },
        )
        explicit_project = await project_repository.create(
            session,
            {
                "name": "explicit-hierarchy-project",
                "description": "Project for explicit hierarchy testing",
                "path": str(config_home / "explicit-hierarchy-project"),
                "is_active": True,
                "is_default": False,
            },
        )

    mock_config = BasicMemoryConfig(
        default_project=default_project.name,
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
                    "bm_write",
                    {
                        "title": "CLI Priority Test",
                        "directory": "test",
                        "content": "# CLI Priority Test",
                        "project": explicit_project.name,
                    },
                )
                response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
                assert f"permalink: {cli_project.name}/" in response_text

    finally:
        del os.environ["BASIC_MEMORY_MCP_PROJECT"]

    # Test 2: Explicit project (medium priority)
    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "bm_write",
                {
                    "title": "Explicit Priority Test",
                    "directory": "test",
                    "content": "# Explicit Priority Test",
                    "project": explicit_project.name,
                },
            )
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
            assert f"permalink: {explicit_project.name}/" in response_text

    # Multiple addressable projects require an explicit scope even with a default.
    with patch.object(ConfigManager, "config", mock_config):
        async with Client(mcp_server) as client:
            with pytest.raises(ToolError, match="Missing required keyword only argument"):
                await client.call_tool(
                    "bm_write",
                    {
                        "title": "Default Priority Test",
                        "directory": "test",
                        "content": "# Default Priority Test",
                    },
                )
            result = await client.call_tool(
                "bm_write",
                {
                    "project": default_project.name,
                    "title": "Default Priority Test",
                    "directory": "test",
                    "content": "# Default Priority Test",
                },
            )
            assert f"permalink: {default_project.name}/" in result.content[0].text
