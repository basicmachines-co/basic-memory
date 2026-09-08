"""Locked-note refusals must reach the agent through the real MCP result."""

import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastmcp import Client, FastMCP
from mcp.types import TextContent
import pytest

from basic_memory.models import Project


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize("operation", ["edit_note", "write_note", "delete_note"])
async def test_locked_note_mutation_returns_visible_refusal(
    mcp_server: FastMCP,
    app: FastAPI,
    test_project: Project,
    output_format: Literal["text", "json"],
    operation: Literal["edit_note", "write_note", "delete_note"],
) -> None:
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Protected Runbook",
                "directory": "protected",
                "content": "---\nlocked: true\n---\n\nKeep this exact content.\n",
                "output_format": "json",
            },
        )
        assert isinstance(created.content[0], TextContent)
        note = json.loads(created.content[0].text)
        path = Path(test_project.path) / note["file_path"]
        original = path.read_bytes()
        arguments: dict[str, object] = {
            "project": test_project.name,
            "output_format": output_format,
        }
        if operation == "write_note":
            arguments.update(
                title="Protected Runbook",
                directory="protected",
                content="Replacement",
                overwrite=True,
            )
        else:
            arguments["identifier"] = note["permalink"]
            if operation == "edit_note":
                arguments.update(operation="append", content="Sneaky edit")
        result = await client.call_tool(operation, arguments, raise_on_error=False)
        assert isinstance(result.content[0], TextContent)
        refusal = result.content[0].text
        assert "locked: true" in refusal
        if output_format == "json" and not result.is_error:
            payload = json.loads(refusal)
            assert payload["error"]
            assert not payload.get("success")
            assert payload.get("checksum") is None
        else:
            assert result.is_error or "error" in refusal.lower() or "failed" in refusal.lower()
        assert path.read_bytes() == original
