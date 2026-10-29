"""MCP tools must not interpret remote project paths on their own host."""

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from httpx import AsyncClient, MockTransport, Request, Response

from basic_memory.mcp.tools import bm_write
from basic_memory.schemas.project_info import ProjectItem


@pytest.mark.parametrize("tool", ["read_note", "bm_write"])
async def test_remote_tool_reaches_api_despite_host_symlink(
    tool: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # bm_write routes through note_targets; read_note owns its own routing.
    module_name = "mcp.note_targets" if tool == "bm_write" else f"mcp.tools.{tool}"
    module = importlib.import_module(f"basic_memory.{module_name}")
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

        async def resolve_path(
            client: AsyncClient, path: str, *args: object, **kwargs: object
        ) -> tuple[ProjectItem, str, bool]:
            return project, path, False

        monkeypatch.setattr(module, "get_project_client", routed)
        if tool == "read_note":
            monkeypatch.setattr(module, "resolve_project_and_path", resolve_path)
        if tool == "read_note":
            result = await module.read_note(
                "escape/note.md", project="project", output_format="json"
            )
            assert result["error"] == "NOTE_NOT_FOUND"
        else:
            with pytest.raises(ToolError, match="not found in object storage"):
                await bm_write(
                    content="body", identifier="escape/note.md", project="project", context=None
                )
    assert requests, "validation must reach the selected API instead of probing local symlinks"
