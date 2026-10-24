"""Native bm_* tools must win collisions with prefixed compatibility tools."""

from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, Tool
from tau import extension
from tau.bridge import McpConnection, Settings
from tau_coding.extensions import ExtensionAPI, ExtensionRuntime


@pytest.mark.parametrize("native_first", [False, True])
async def test_native_tools_keep_their_schema_and_dispatch(
    monkeypatch: pytest.MonkeyPatch, native_first: bool
) -> None:
    legacy = [
        Tool(name=name, input_schema={"type": "object", "properties": {"meta": {}}})
        for name in ("find", "man")
    ]
    native = [
        Tool(name=f"bm_{name}", input_schema={"type": "object", "properties": {"filters": {}}})
        for name in ("find", "man")
    ]
    tools = native + legacy if native_first else legacy + native
    tools.append(Tool(name="extra", input_schema={"type": "object"}))
    connection = MagicMock(spec=McpConnection)
    connection.call = AsyncMock(return_value=CallToolResult(content=[]))
    monkeypatch.setattr(extension, "load_settings", lambda: Settings())
    monkeypatch.setattr(extension, "discover_sync", lambda settings: tools)
    monkeypatch.setattr(extension, "McpConnection", lambda settings: connection)
    runtime = ExtensionRuntime(built_in_extensions=())
    api = MagicMock(spec=ExtensionAPI)
    api.register_tool.side_effect = partial(runtime.register_tool, "basic-memory", "Basic Memory")

    extension.setup(api)

    registered = {tool.name: tool for tool in runtime.compose_tools([])}
    assert set(registered) == {"bm_find", "bm_man", "bm_extra"}
    assert not runtime.diagnostics
    for tool in native:
        assert registered[tool.name].parameters == tool.input_schema
        await registered[tool.name].execute("test-call", {"filters": {}})
        connection.call.assert_awaited_with(tool.name, {"filters": {}})
