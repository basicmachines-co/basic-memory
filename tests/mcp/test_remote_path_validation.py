"""MCP tools must not interpret remote project paths on their own host."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from httpx import AsyncClient, MockTransport, Request, Response

from basic_memory.mcp import note_targets
from basic_memory.mcp.tools import bm_read, bm_write
from basic_memory.schemas.project_info import ProjectItem


@pytest.mark.parametrize("tool", ["bm_read", "bm_write"])
async def test_remote_tool_reaches_api_despite_host_symlink(
    tool: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both tools route through note_targets.
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    project = ProjectItem(id=1, external_id="project-id", name="project", path=str(root))
    requests: list[Request] = []

    def respond(request: Request) -> Response:
        requests.append(request)
        return Response(404, json={"detail": "not found in object storage"})

    async with AsyncClient(transport=MockTransport(respond), base_url="https://remote") as client:

        @asynccontextmanager
        async def routed(
            *args: object, **kwargs: object
        ) -> AsyncIterator[tuple[AsyncClient, ProjectItem]]:
            yield client, project

        monkeypatch.setattr(note_targets, "get_project_client", routed)
        if tool == "bm_read":
            with pytest.raises(ToolError, match="^NOT_FOUND:"):
                await bm_read("escape/note.md", project="project", context=None)
        else:
            with pytest.raises(ToolError, match="not found in object storage"):
                await bm_write(
                    content="body", identifier="escape/note.md", project="project", context=None
                )
    assert requests, "validation must reach the selected API instead of probing local symlinks"
