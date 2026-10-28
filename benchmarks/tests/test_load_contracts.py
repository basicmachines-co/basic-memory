"""Load harnesses send equivalent operations through version-specific tools."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.client.session import ClientSession
from mcp.types import CallToolResult, TextContent


def load_script(name: str):
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


read_load = load_script("read_load_bench")
write_load = load_script("write_load_bench")


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["bm", "legacy"])
async def test_read_seed_and_search_use_selected_contract(surface: str):
    session = AsyncMock(spec=ClientSession)
    session.call_tool.return_value = CallToolResult(
        content=[], structuredContent={"permalink": "notes/example", "total": 7}
    )
    target = await read_load.write_target(
        session, project="test", size_bytes=1024, index=0, surface=surface
    )
    assert target.identifier == "notes/example"
    assert session.call_tool.call_args.args[0] == (
        "write_note" if surface == "legacy" else "bm_write"
    )
    assert await read_load.searchable_count(session, "test", surface) == 7
    name, arguments = session.call_tool.call_args.args
    assert name == ("search_notes" if surface == "legacy" else "bm_search")
    assert arguments["search_type" if surface == "legacy" else "mode"] == "text"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["bm", "legacy"])
async def test_measured_reads_validate_content_on_both_surfaces(surface: str):
    session = AsyncMock(spec=ClientSession)
    session.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text="marker")]
    )
    targets = [read_load.ReadTarget("notes/example", "marker", 1024)]
    _, errors, invalid, response_bytes = await read_load.read_burst(
        session, project="test", targets=targets, count=2, concurrency=1, seed=42, surface=surface
    )
    assert errors == invalid == 0
    assert response_bytes == 12
    assert session.call_tool.call_args.args[0] == (
        "read_note" if surface == "legacy" else "bm_read"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["bm", "legacy"])
async def test_measured_writes_use_selected_contract(surface: str):
    session = AsyncMock(spec=ClientSession)
    session.call_tool.return_value = CallToolResult(content=[])
    latencies, errors = await write_load.write_burst(
        session, project="test", level=1, count=2, concurrency=1, surface=surface
    )
    assert len(latencies) == 2 and errors == 0
    assert session.call_tool.call_args.args[0] == (
        "write_note" if surface == "legacy" else "bm_write"
    )
