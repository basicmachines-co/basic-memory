"""
Pytest configuration: stub Hermes-internal imports so the plugin loads
without a Hermes install, and expose the loaded plugin module as a fixture.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types

import pytest


def _stub_hermes_modules() -> None:
    """
    The plugin imports `agent.memory_provider.MemoryProvider` and
    `tools.registry.tool_error`. These are provided by Hermes at runtime,
    not as a pip-installable package. Stub them before module load so unit
    tests don't need Hermes installed.
    """
    agent_mod = types.ModuleType("agent")
    agent_mp_mod = types.ModuleType("agent.memory_provider")

    class _StubMemoryProvider:
        """Stand-in for `agent.memory_provider.MemoryProvider`."""

    agent_mp_mod.MemoryProvider = _StubMemoryProvider
    sys.modules.setdefault("agent", agent_mod)
    sys.modules.setdefault("agent.memory_provider", agent_mp_mod)

    tools_mod = types.ModuleType("tools")
    tools_registry_mod = types.ModuleType("tools.registry")

    def _tool_error(msg: str) -> str:
        return json.dumps({"error": str(msg)})

    tools_registry_mod.tool_error = _tool_error
    sys.modules.setdefault("tools", tools_mod)
    sys.modules.setdefault("tools.registry", tools_registry_mod)


_stub_hermes_modules()


_PLUGIN_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "__init__.py")
_spec = importlib.util.spec_from_file_location("hermes_basic_memory_plugin", _PLUGIN_PATH)
assert _spec is not None and _spec.loader is not None
_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_plugin)


@pytest.fixture
def bm():
    """The loaded plugin module."""
    return _plugin


@pytest.fixture(autouse=True)
def reset_plugin_lifecycle_state():
    """Keep module-level provider singletons isolated between tests."""
    if hasattr(_plugin, "_active_providers"):
        _plugin._active_providers.clear()
    if hasattr(_plugin, "_provider_singleton"):
        setattr(_plugin, "_provider_singleton", None)
    yield
    if hasattr(_plugin, "_active_providers"):
        _plugin._active_providers.clear()
    if hasattr(_plugin, "_provider_singleton"):
        setattr(_plugin, "_provider_singleton", None)


# ---- Synthetic MCP CallToolResult objects (no MCP SDK needed at test time) ----


class FakeContent:
    """Stand-in for `mcp.types.TextContent`."""

    def __init__(self, text: str):
        self.text = text


class FakeCallToolResult:
    """Stand-in for `mcp.types.CallToolResult`."""

    def __init__(self, content_texts, is_error: bool = False):
        self.content = [FakeContent(t) for t in content_texts]
        self.isError = is_error


@pytest.fixture
def fake_result():
    return FakeCallToolResult


# ---- Actor test helpers ----


class FakeSession:
    """
    Stand-in for `mcp.ClientSession`. `call_tool` is an async coroutine
    that records every call and returns a configurable CallToolResult.

    - default_response: dict serialized to JSON in the result text
    - hang: when True, call_tool sleeps long enough to force a client-side timeout
    - is_error: when True, return value has isError=True
    """

    def __init__(self, default_response=None, hang: bool = False, is_error: bool = False):
        self.default_response = default_response if default_response is not None else {"ok": True}
        self.hang = hang
        self.is_error = is_error
        self.calls: list = []
        self.was_cancelled = False
        self._stub_handlers: dict = {}

    def stub(self, tool_name: str, handler):
        """Register a per-tool handler. Handler receives (args) and returns a response dict (or raises)."""
        self._stub_handlers[tool_name] = handler

    async def call_tool(self, name: str, args: dict):
        self.calls.append((name, dict(args)))
        if self.hang:
            try:
                import asyncio as _a

                await _a.sleep(60)
            except BaseException as e:
                # Track cooperative cancellation so tests can verify cleanup
                if type(e).__name__ in ("CancelledError",):
                    self.was_cancelled = True
                raise
        if name in self._stub_handlers:
            response = self._stub_handlers[name](args)
        else:
            response = self.default_response
        return FakeCallToolResult([json.dumps(response)], is_error=self.is_error)


def make_scripted_actor(
    bm, session=None, raise_at_init: BaseException | None = None, fake_tools: list | None = None
):
    """
    Build an actor whose `_main()` is replaced with a deterministic version.
    The fake `_main` mirrors the production happy path (sets _session, _stop_future,
    fills _tools_cache, signals ready, awaits stop) but skips the stdio subprocess.

    raise_at_init: if set, raised inside _main so we can test failure paths.
    """
    import asyncio

    actor = bm._BmMcpActor(["fake-bm", "mcp"])
    sess = session or FakeSession()

    async def _fake_main():
        if raise_at_init is not None:
            actor._init_error = raise_at_init
            actor._ready.set()
            raise raise_at_init
        actor._session = sess
        actor._stop_future = asyncio.get_running_loop().create_future()
        actor._tools_cache = fake_tools or [
            {"name": n, "description": ""}
            for n in [
                "search_notes",
                "read_note",
                "write_note",
                "edit_note",
                "build_context",
                "delete_note",
                "move_note",
            ]
        ]
        actor._ready.set()
        await actor._stop_future

    actor._main = _fake_main  # type: ignore[assignment]
    actor._test_session = sess  # convenience handle for assertions
    return actor
