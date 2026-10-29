"""Readiness must survive MCP serialization and account-wide search."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from sqlalchemy import update

from basic_memory import db
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.mcp.tools import bm_project, bm_search, bm_write
from basic_memory.models import Project


@pytest.mark.parametrize("tool", ["bm_search", "bm_project"])
@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize("page", [1, 2])
async def test_index_required_through_mcp(mcp, client, test_project, tool, output_format, page):
    """Neither JSON nor a later page may disguise a never-indexed project as empty."""
    params = {"project": test_project.name, "output_format": output_format, "page": page}
    if tool == "bm_search":
        params.update(query="missing", mode="text")
    else:
        params.update(command="recent", items=["observation"])
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(tool, params)
    assert "# Project Index Required" in result.content[0].text
    assert "never been indexed" in result.content[0].text
    assert "For a local project:" in result.content[0].text
    assert "For a cloud project:" in result.content[0].text


async def test_all_project_search_preserves_index_required(client, test_project):
    text = await bm_search(query="missing", mode="text", all_projects=True)
    payload = await bm_search(query="missing", mode="text", all_projects=True, output_format="json")

    assert isinstance(text, str)
    assert text.startswith("# Project Index Required")
    assert test_project.name in text
    assert isinstance(payload, dict)
    assert payload["results"] == []
    assert payload["guidance"].startswith("# Project Index Required")


async def test_readiness_failure_is_not_an_empty_success(client, test_project, monkeypatch):
    # Failure injection: no real path makes the status endpoint fail on demand.
    monkeypatch.setattr(
        ProjectClient, "get_status", AsyncMock(side_effect=ToolError("readiness unavailable"))
    )

    with pytest.raises(ToolError, match="readiness unavailable"):
        await bm_search(project=test_project.name, query="missing", output_format="json")
    with pytest.raises(ToolError, match="readiness unavailable"):
        await bm_project(command="recent", project=test_project.name, output_format="json")


async def test_nonempty_reads_skip_status(client, test_project, monkeypatch):
    await bm_write(
        project=test_project.name, title="Visible", content="Visible content", directory="notes"
    )
    # Failure injection: any status call fails the test.
    status = AsyncMock(side_effect=AssertionError("nonempty reads must not scan status"))
    monkeypatch.setattr(ProjectClient, "get_status", status)

    found = await bm_search(
        project=test_project.name, query="Visible", mode="text", output_format="json"
    )
    recent = await bm_project(command="recent", project=test_project.name, output_format="json")

    assert isinstance(found, dict) and found["results"]
    assert isinstance(recent, dict) and recent["results"]
    status.assert_not_called()


async def test_indexed_pending_misses_stay_empty(client, test_project, session_maker, config_home):
    """Pending new files do not revoke an already-indexed project's ordinary misses."""
    (config_home / "unindexed.md").write_text("# A file awaiting the next pass\n")
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            update(Project)
            .where(Project.id == test_project.id)
            .values(last_indexed_at=datetime.now(timezone.utc))
        )

    found = await bm_search(
        project=test_project.name, query="missing", mode="text", output_format="json"
    )
    recent = await bm_project(command="recent", project=test_project.name, output_format="json")

    assert isinstance(found, dict) and found["results"] == []
    assert "guidance" not in found
    assert recent == {"results": [], "page": 1, "page_size": 10, "has_more": False}
