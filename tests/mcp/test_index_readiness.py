"""Readiness must survive MCP serialization and account-wide search."""

from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import update

from basic_memory import db
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.mcp.tools import recent_activity, search_notes, write_note
from basic_memory.models import Project


@pytest.mark.parametrize("tool", ["search_notes", "recent_activity"])
@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize("page", [1, 2])
async def test_index_required_through_mcp(mcp, client, test_project, tool, output_format, page):
    """Neither JSON nor a later page may disguise a never-indexed project as empty."""
    params = {"project": test_project.name, "output_format": output_format, "page": page}
    if tool == "search_notes":
        params.update(query="missing", search_type="text")
    else:
        params.update(type="observation")
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(tool, params)
    assert "# Project Index Required" in result.content[0].text
    assert "never been indexed" in result.content[0].text
    assert "For a local project:" in result.content[0].text
    assert "For a cloud project:" in result.content[0].text


@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_all_project_search_preserves_index_required(client, test_project, output_format):
    result = await search_notes(
        query="missing", search_type="text", search_all_projects=True, output_format=output_format
    )
    assert isinstance(result, str)
    assert result.startswith("# Project Index Required")
    assert test_project.name in result


async def test_readiness_failure_is_not_an_empty_success(client, test_project, monkeypatch):
    monkeypatch.setattr(
        ProjectClient, "get_status", AsyncMock(side_effect=ToolError("readiness unavailable"))
    )
    result = await search_notes(project=test_project.name, query="missing", output_format="json")
    assert isinstance(result, str)
    assert "Search Failed" in result
    assert "readiness unavailable" in result
    with pytest.raises(ToolError, match="readiness unavailable"):
        await recent_activity(project=test_project.name, output_format="json")


async def test_nonempty_reads_skip_status(client, test_project, monkeypatch):
    await write_note(
        project=test_project.name, title="Visible", content="Visible content", directory="notes"
    )
    status = AsyncMock(side_effect=AssertionError("nonempty reads must not scan status"))
    monkeypatch.setattr(ProjectClient, "get_status", status)
    result = await search_notes(
        project=test_project.name, query="Visible", search_type="text", output_format="json"
    )
    assert isinstance(result, dict) and result["results"]
    assert isinstance(await recent_activity(project=test_project.name, output_format="json"), list)
    status.assert_not_called()


async def test_indexed_pending_misses_stay_empty(client, test_project, session_maker, config_home):
    """Pending new files do not revoke an already-indexed project's ordinary misses."""
    from datetime import datetime, timezone

    (config_home / "unindexed.md").write_text("# A file awaiting the next pass\n")
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            update(Project)
            .where(Project.id == test_project.id)
            .values(last_indexed_at=datetime.now(timezone.utc))
        )
    result = await search_notes(
        project=test_project.name, query="missing", search_type="text", output_format="json"
    )
    assert isinstance(result, dict) and result["results"] == []
    assert await recent_activity(project=test_project.name, output_format="json") == []


@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_activity_discovery_preserves_index_required(
    client, test_project, monkeypatch, output_format
):
    import importlib

    activity_module = importlib.import_module("basic_memory.mcp.tools.recent_activity")
    monkeypatch.setattr(activity_module, "resolve_project_parameter", AsyncMock(return_value=None))
    result = await recent_activity(output_format=output_format)
    assert isinstance(result, str)
    assert result.startswith("# Project Index Required")
    assert test_project.name in result
