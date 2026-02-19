"""Integration tests for MCP `output_format="json"` responses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

from basic_memory.mcp.clients.knowledge import KnowledgeClient


def _json_content(tool_result) -> dict | list:
    """Parse a FastMCP tool result content block into JSON."""
    assert len(tool_result.content) == 1
    assert tool_result.content[0].type == "text"
    return json.loads(tool_result.content[0].text)  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_write_note_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Write",
                "directory": "json-int",
                "content": "# JSON Integration Write\n\nBody",
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert payload["title"] == "JSON Integration Write"
        assert payload["action"] in ("created", "updated")
        assert payload["permalink"]
        assert payload["file_path"]
        assert "checksum" in payload


@pytest.mark.asyncio
async def test_read_note_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Read",
                "directory": "json-int",
                "content": "# JSON Integration Read\n\nBody",
            },
        )

        result = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "json-int/json-integration-read",
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert payload["title"] == "JSON Integration Read"
        assert payload["permalink"]
        assert payload["file_path"]
        assert isinstance(payload["content"], str)
        assert "frontmatter" in payload


@pytest.mark.asyncio
async def test_edit_note_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Edit",
                "directory": "json-int",
                "content": "# JSON Integration Edit\n\nBody",
            },
        )

        result = await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "json-int/json-integration-edit",
                "operation": "append",
                "content": "\n\nAppended",
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert payload["title"] == "JSON Integration Edit"
        assert payload["operation"] == "append"
        assert payload["permalink"]
        assert payload["file_path"]
        assert "checksum" in payload


@pytest.mark.asyncio
async def test_recent_activity_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Recent",
                "directory": "json-int",
                "content": "# JSON Integration Recent\n\nBody",
            },
        )

        result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "timeframe": "7d",
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert isinstance(payload, list)
        assert any(item.get("title") == "JSON Integration Recent" for item in payload)
        for item in payload:
            assert set(["type", "title", "permalink", "file_path", "created_at"]).issubset(
                item.keys()
            )


@pytest.mark.asyncio
async def test_recent_activity_json_output_for_relation_and_observation_types(
    mcp_server, app, test_project
):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Type Source",
                "directory": "json-int",
                "content": (
                    "# JSON Integration Type Source\n\n"
                    "- [note] observation from source\n"
                    "- links_to [[JSON Integration Type Target]]"
                ),
            },
        )
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Type Target",
                "directory": "json-int",
                "content": "# JSON Integration Type Target\n\nBody",
            },
        )

        relation_result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "timeframe": "7d",
                "type": "relation",
                "output_format": "json",
            },
        )
        relation_payload = _json_content(relation_result)
        assert isinstance(relation_payload, list)
        assert relation_payload
        assert all(item.get("type") == "relation" for item in relation_payload)
        for item in relation_payload:
            assert set(["type", "title", "permalink", "file_path", "created_at"]).issubset(
                item.keys()
            )

        observation_result = await client.call_tool(
            "recent_activity",
            {
                "project": test_project.name,
                "timeframe": "7d",
                "type": "observation",
                "output_format": "json",
            },
        )
        observation_payload = _json_content(observation_result)
        assert isinstance(observation_payload, list)
        assert observation_payload
        assert all(item.get("type") == "observation" for item in observation_payload)
        for item in observation_payload:
            assert set(["type", "title", "permalink", "file_path", "created_at"]).issubset(
                item.keys()
            )


@pytest.mark.asyncio
async def test_list_memory_projects_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "list_memory_projects",
            {"output_format": "json"},
        )

        payload = _json_content(result)
        assert isinstance(payload, dict)
        assert "projects" in payload
        assert any(project["name"] == test_project.name for project in payload["projects"])
        assert "default_project" in payload
        assert "constrained_project" in payload


@pytest.mark.asyncio
async def test_create_memory_project_json_output_is_idempotent(
    mcp_server, app, test_project, tmp_path
):
    async with Client(mcp_server) as client:
        project_name = "json-int-created"
        project_path = str(tmp_path.parent / (tmp_path.name + "-projects") / "json-int-created")

        first = await client.call_tool(
            "create_memory_project",
            {
                "project_name": project_name,
                "project_path": project_path,
                "output_format": "json",
            },
        )
        first_payload = _json_content(first)
        assert first_payload["name"] == project_name
        # Normalize path separators for cross-platform compatibility.
        assert Path(first_payload["path"]) == Path(project_path)
        assert first_payload["created"] is True
        assert first_payload["already_exists"] is False

        second = await client.call_tool(
            "create_memory_project",
            {
                "project_name": project_name,
                "project_path": str(
                    tmp_path.parent / (tmp_path.name + "-projects") / "json-int-created-second"
                ),
                "output_format": "json",
            },
        )
        second_payload = _json_content(second)
        assert second_payload["name"] == project_name
        assert second_payload["created"] is False
        assert second_payload["already_exists"] is True


@pytest.mark.asyncio
async def test_delete_note_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Delete",
                "directory": "json-int",
                "content": "# JSON Integration Delete\n\nBody",
            },
        )

        result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "json-int/json-integration-delete",
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert payload["deleted"] is True
        assert payload["title"] == "JSON Integration Delete"
        assert payload["permalink"]
        assert payload["file_path"]


@pytest.mark.asyncio
async def test_delete_note_directory_json_output_failure_is_structured(
    mcp_server, app, test_project, monkeypatch
):
    async def mock_delete_directory(self, directory: str):
        raise RuntimeError("simulated directory delete failure")

    monkeypatch.setattr(KnowledgeClient, "delete_directory", mock_delete_directory)

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "delete_note",
            {
                "project": test_project.name,
                "identifier": "json-int",
                "is_directory": True,
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert payload["deleted"] is False
        assert payload["is_directory"] is True
        assert payload["identifier"] == "json-int"
        assert "simulated directory delete failure" in payload["error"]


@pytest.mark.asyncio
async def test_move_note_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Move",
                "directory": "json-int",
                "content": "# JSON Integration Move\n\nBody",
            },
        )

        result = await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "json-int/json-integration-move",
                "destination_path": "json-int/moved/json-integration-move.md",
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert payload["moved"] is True
        assert payload["title"] == "JSON Integration Move"
        assert payload["source"] == "json-int/json-integration-move"
        assert payload["destination"] == "json-int/moved/json-integration-move.md"
        assert payload["permalink"]
        assert payload["file_path"]


@pytest.mark.asyncio
async def test_build_context_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Context",
                "directory": "json-int",
                "content": "# JSON Integration Context\n\nBody",
            },
        )

        result = await client.call_tool(
            "build_context",
            {
                "project": test_project.name,
                "url": "memory://json-int/json-integration-context",
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert isinstance(payload, dict)
        assert "results" in payload
        assert "metadata" in payload


@pytest.mark.asyncio
async def test_search_notes_json_output(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "JSON Integration Search",
                "directory": "json-int",
                "content": "# JSON Integration Search\n\nBody",
            },
        )

        result = await client.call_tool(
            "search_notes",
            {
                "project": test_project.name,
                "query": "JSON Integration Search",
                "output_format": "json",
            },
        )

        payload = _json_content(result)
        assert isinstance(payload, dict)
        assert "results" in payload
        assert isinstance(payload["results"], list)
        assert any(item.get("title") == "JSON Integration Search" for item in payload["results"])
