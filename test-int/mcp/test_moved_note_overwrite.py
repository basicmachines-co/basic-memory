"""An overwrite must not silently create a shadow of a renamed note."""

import json
from pathlib import Path

import pytest
from fastmcp import Client
from basic_memory.config import ConfigManager


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_overwrite_after_rename_refuses_without_changing_files(
    mcp_server, app, test_project, app_config, output_format
):
    app_config.update_permalinks_on_move = False
    ConfigManager().save_config(app_config)
    async with Client(mcp_server) as client:
        arguments = {
            "project": test_project.name,
            "title": "song-sketching",
            "directory": "app/probe",
            "content": "# original\nfirst body",
        }
        await client.call_tool("write_note", arguments)
        await client.call_tool(
            "move_note",
            {
                "project": test_project.name,
                "identifier": "song-sketching",
                "destination_path": "app/probe/SKILL.md",
            },
        )
        moved = Path(test_project.path) / "app/probe/SKILL.md"
        original = moved.read_bytes()
        result = await client.call_tool(
            "write_note",
            {
                **arguments,
                "content": "# replacement\nsecond body",
                "overwrite": True,
                "output_format": output_format,
            },
        )
        assert result.content[0].type == "text"
        if output_format == "json":
            payload = json.loads(result.content[0].text)
            assert payload["action"] == "conflict"
            assert payload["error"] == "NOTE_PATH_CONFLICT"
            assert payload["file_path"] == "app/probe/SKILL.md"
        else:
            assert "different path" in result.content[0].text
            assert "app/probe/SKILL.md" in result.content[0].text
        assert moved.read_bytes() == original
        assert sorted(path.name for path in moved.parent.glob("*.md")) == ["SKILL.md"]
