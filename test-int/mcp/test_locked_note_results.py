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
@pytest.mark.parametrize("operation", ["bm_edit", "bm_write", "bm_delete"])
async def test_locked_note_mutation_returns_visible_refusal(
    mcp_server: FastMCP,
    app: FastAPI,
    test_project: Project,
    output_format: Literal["text", "json"],
    operation: Literal["bm_edit", "bm_write", "bm_delete"],
) -> None:
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "bm_write",
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
        if operation == "bm_write":
            arguments.update(
                title="Protected Runbook",
                directory="protected",
                content="Replacement",
                overwrite=True,
            )
        else:
            arguments["identifier"] = note["permalink"]
            if operation == "bm_edit":
                arguments.update(command="append", content="Sneaky edit")
        result = await client.call_tool(operation, arguments, raise_on_error=False)
        assert isinstance(result.content[0], TextContent)
        refusal = result.content[0].text
        assert "locked: true" in refusal
        assert result.is_error
        if output_format == "json":
            assert json.loads(refusal)["error"] == "LOCKED"
        assert path.read_bytes() == original
