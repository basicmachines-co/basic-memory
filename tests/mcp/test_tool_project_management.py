"""Tests for MCP project management tools."""

import pytest
from sqlalchemy import select

from basic_memory import db
from basic_memory.mcp.tools import list_memory_projects, create_memory_project, delete_project
from basic_memory.models.project import Project


@pytest.mark.asyncio
async def test_list_memory_projects_unconstrained(app, test_project):
    result = await list_memory_projects.fn()
    assert "Available projects:" in result
    assert f"• {test_project.name}" in result


@pytest.mark.asyncio
async def test_list_memory_projects_shows_display_name(app, client, test_project):
    """When a project has display_name set, list_memory_projects shows 'display_name (name)' format."""
    # Inject display_name into the project list response by patching the API response.
    # In production, the cloud proxy adds display_name to the JSON before deserialization.
    from unittest.mock import AsyncMock, patch
    from basic_memory.schemas.project_info import ProjectItem, ProjectList

    mock_project = ProjectItem(
        id=1,
        external_id="00000000-0000-0000-0000-000000000001",
        name="private-fb83af23",
        path="/tmp/private",
        is_default=False,
        display_name="My Notes",
        is_private=True,
    )
    regular_project = ProjectItem(
        id=2,
        external_id="00000000-0000-0000-0000-000000000002",
        name="main",
        path="/tmp/main",
        is_default=True,
    )
    mock_list = ProjectList(
        projects=[regular_project, mock_project],
        default_project="main",
    )

    with patch(
        "basic_memory.mcp.clients.project.ProjectClient.list_projects",
        new_callable=AsyncMock,
        return_value=mock_list,
    ):
        result = await list_memory_projects.fn()

    # Regular project shows just the name
    assert "• main\n" in result
    # Private project shows display_name with slug in parentheses
    assert "• My Notes (private-fb83af23)" in result


@pytest.mark.asyncio
async def test_list_memory_projects_no_display_name_shows_name_only(app, client, test_project):
    """When a project has no display_name, list_memory_projects shows just the name."""
    from unittest.mock import AsyncMock, patch
    from basic_memory.schemas.project_info import ProjectItem, ProjectList

    project = ProjectItem(
        id=1,
        external_id="00000000-0000-0000-0000-000000000001",
        name="my-project",
        path="/tmp/my-project",
        is_default=True,
    )
    mock_list = ProjectList(projects=[project], default_project="my-project")

    with patch(
        "basic_memory.mcp.clients.project.ProjectClient.list_projects",
        new_callable=AsyncMock,
        return_value=mock_list,
    ):
        result = await list_memory_projects.fn()

    assert "• my-project\n" in result
    # Should NOT have parenthetical format
    assert "(" not in result.split("• my-project")[1].split("\n")[0]


@pytest.mark.asyncio
async def test_list_memory_projects_constrained_env(monkeypatch, app, test_project):
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)
    result = await list_memory_projects.fn()
    assert f"Project: {test_project.name}" in result
    assert "constrained to a single project" in result


@pytest.mark.asyncio
async def test_create_and_delete_project_and_name_match_branch(
    app, tmp_path_factory, session_maker
):
    # Create a project through the tool (exercises POST + response formatting).
    project_root = tmp_path_factory.mktemp("extra-project-home")
    result = await create_memory_project.fn(
        project_name="My Project",
        project_path=str(project_root),
        set_default=False,
    )
    assert result.startswith("✓")
    assert "My Project" in result

    # Make permalink intentionally not derived from name so delete_project hits the name-match branch.
    async with db.scoped_session(session_maker) as session:
        project = (
            await session.execute(select(Project).where(Project.name == "My Project"))
        ).scalar_one()
        project.permalink = "custom-permalink"
        await session.commit()

    delete_result = await delete_project.fn("My Project")
    assert delete_result.startswith("✓")
