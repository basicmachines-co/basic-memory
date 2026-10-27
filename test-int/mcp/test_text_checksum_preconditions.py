"""Text write receipts can be used as exact revision preconditions (#1874)."""

import pytest
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from basic_memory.models import Project


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_kind", ["created", "updated", "edited", "auto_created"])
async def test_text_checksum_allows_conditional_overwrite(
    mcp_server: FastMCP, app: FastAPI, test_project: Project, receipt_kind: str
) -> None:
    async with Client(mcp_server) as client:
        if receipt_kind in {"updated", "edited"}:
            await client.call_tool(
                "write_note",
                {
                    "title": "Checksum Probe",
                    "directory": "notes",
                    "content": "Initial content.",
                    "project": test_project.name,
                },
            )
        if receipt_kind in {"created", "updated"}:
            receipt = await client.call_tool(
                "write_note",
                {
                    "title": "Checksum Probe",
                    "directory": "notes",
                    "content": "Original content.",
                    "overwrite": receipt_kind == "updated",
                    "project": test_project.name,
                },
            )
        else:
            receipt = await client.call_tool(
                "edit_note",
                {
                    "identifier": "notes/Checksum Probe",
                    "operation": "append",
                    "content": "Original content.",
                    "project": test_project.name,
                },
            )
        assert isinstance(receipt.data, str)
        checksum = next(
            line.removeprefix("checksum: ")
            for line in receipt.data.splitlines()
            if line.startswith("checksum: ")
        )

        replaced = await client.call_tool(
            "write_note",
            {
                "title": "Checksum Probe",
                "directory": "notes",
                "content": "Replacement content.",
                "overwrite": True,
                "expected_checksum": checksum,
                "output_format": "json",
                "project": test_project.name,
            },
        )
        assert isinstance(replaced.data, dict)
        assert replaced.data["action"] == "updated"
        assert len(checksum) == 64

        with pytest.raises(ToolError, match="revision conflict"):
            await client.call_tool(
                "write_note",
                {
                    "title": "Checksum Probe",
                    "directory": "notes",
                    "content": "Stale replacement.",
                    "overwrite": True,
                    "expected_checksum": checksum,
                    "project": test_project.name,
                },
            )
        current = await client.call_tool(
            "read_note", {"identifier": "Checksum Probe", "project": test_project.name}
        )
        assert isinstance(current.data, str)
        assert "Replacement content." in current.data
        assert "Stale replacement." not in current.data
