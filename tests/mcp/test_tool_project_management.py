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
