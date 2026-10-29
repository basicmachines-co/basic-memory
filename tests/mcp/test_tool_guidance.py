"""Recovery guidance must work for agents that have only the public bm_* tools."""

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import bm_delete, bm_edit, bm_read, bm_write


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_refused_edit_points_to_bm_read(client, test_project, output_format):
    await bm_write(
        identifier="notes/guarded.md", content="Original content", project=test_project.name
    )

    with pytest.raises(ToolError) as refused:
        await bm_edit(
            identifier="notes/guarded.md",
            command="append",
            content="Must not land",
            expected_checksum="stale",
            output_format=output_format,
            project=test_project.name,
        )

    guidance = str(refused.value)
    # JSON output escapes quotes, but the suggested tool and parameter stay the same.
    assert "bm_read(identifier=" in guidance
    assert "read_note(" not in guidance
    assert "project_id=" not in guidance
    assert test_project.name in guidance
    content = await bm_read("notes/guarded.md", project=test_project.external_id)
    assert isinstance(content, str)
    assert "Original content" in content
    assert "Must not land" not in content


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_refused_delete_points_to_bm_read(client, test_project, output_format):
    await bm_write(
        identifier="notes/guarded.md", content="Keep this note", project=test_project.name
    )
    with pytest.raises(ToolError) as refused:
        await bm_delete(
            "notes/guarded.md",
            expected_checksum="stale",
            output_format=output_format,
            project=test_project.name,
        )
    assert "bm_read(identifier=" in str(refused.value)
    assert "read_note(" not in str(refused.value)
    assert "Keep this note" in str(await bm_read("notes/guarded.md", project=test_project.name))
