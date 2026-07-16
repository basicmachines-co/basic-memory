"""
Integration tests for created/modified frontmatter timestamps (#238, #684).

Covers the full file <-> DB round trip: BM auto-fills created/modified on
create, preserves an existing created across edits, and bumps modified on
every write it performs unless the request explicitly supplies its own.
"""

from datetime import datetime
from pathlib import Path

import pytest
from fastmcp import Client

from basic_memory.file_utils import parse_frontmatter


def _read_frontmatter(test_project, relative_path: str) -> dict:
    file_path = Path(test_project.path) / relative_path
    return parse_frontmatter(file_path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_write_note_auto_fills_created_and_modified(mcp_server, app, test_project):
    """A note created without explicit timestamps gets created/modified stamped in frontmatter."""

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Auto Timestamp Note",
                "directory": "timestamps",
                "content": "# Auto Timestamp Note\n\nBody text.",
            },
        )

    frontmatter = _read_frontmatter(test_project, "timestamps/Auto Timestamp Note.md")

    assert "created" in frontmatter
    assert "modified" in frontmatter
    # Both round-trip through ISO 8601 and a freshly created note stamps them identically.
    assert datetime.fromisoformat(frontmatter["created"]) == datetime.fromisoformat(
        frontmatter["modified"]
    )


@pytest.mark.asyncio
async def test_write_note_honors_user_supplied_date_only_created(mcp_server, app, test_project):
    """A user-supplied date-only `created` value in frontmatter is preserved verbatim (#238)."""

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Historical Import",
                "directory": "timestamps",
                "content": "---\ncreated: 2024-03-15\n---\n# Historical Import\n\nImported body.",
            },
        )

    frontmatter = _read_frontmatter(test_project, "timestamps/Historical Import.md")

    # BM never overwrites a timestamp the frontmatter already carries.
    assert frontmatter["created"] == "2024-03-15"
    assert "modified" in frontmatter


@pytest.mark.asyncio
async def test_edit_note_preserves_created_and_bumps_modified(mcp_server, app, test_project):
    """Editing a note keeps its created timestamp but bumps modified (#684)."""

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Edited Timestamp Note",
                "directory": "timestamps",
                "content": "# Edited Timestamp Note\n\nOriginal body.",
            },
        )

        before = _read_frontmatter(test_project, "timestamps/Edited Timestamp Note.md")

        await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "timestamps/edited-timestamp-note",
                "operation": "append",
                "content": "\n\nAppended body.",
            },
        )

    after = _read_frontmatter(test_project, "timestamps/Edited Timestamp Note.md")

    assert after["created"] == before["created"]
    assert datetime.fromisoformat(after["modified"]) >= datetime.fromisoformat(
        before["modified"]
    )


@pytest.mark.asyncio
async def test_edit_note_preserves_hand_set_created_across_edit(mcp_server, app, test_project):
    """A hand-set created date survives a subsequent edit (#238 + #684 agreement)."""

    async with Client(mcp_server) as client:
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Backdated Note",
                "directory": "timestamps",
                "content": "---\ncreated: 2020-01-01T09:00:00\n---\n# Backdated Note\n\nBody.",
            },
        )

        before = _read_frontmatter(test_project, "timestamps/Backdated Note.md")
        assert before["created"] == "2020-01-01T09:00:00"

        await client.call_tool(
            "edit_note",
            {
                "project": test_project.name,
                "identifier": "timestamps/backdated-note",
                "operation": "append",
                "content": "\n\nMore body.",
            },
        )

    after = _read_frontmatter(test_project, "timestamps/Backdated Note.md")

    assert after["created"] == "2020-01-01T09:00:00"
    assert datetime.fromisoformat(after["modified"]) >= datetime.fromisoformat(
        before["modified"]
    )
