"""A persistent MCP connection with one task owning the stdio context lifetime."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, PaginatedRequestParams, Tool
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str = Field(default="bm", min_length=1)
    args: list[str] = Field(default_factory=lambda: ["mcp", "--transport", "stdio"])
    project: str | None = Field(default=None, min_length=1)
    auto_recall: StrictBool = True
    checkpoint_on_compact: StrictBool = True
    capture_knowledge: StrictBool = True
    summarize_on_shutdown: StrictBool = True
    checkpoint_folder: str = "tau/checkpoints"
    summary_timeout_seconds: float = Field(default=60, gt=0, le=300)
    summary_chunk_chars: int = Field(default=16000, ge=1000, le=50000)
    capture_transcript: StrictBool = False
    capture_folder: str = "tau/transcripts"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    recall_chars: int = Field(default=12000, ge=100, le=50000)


def load_settings() -> Settings:
    # Only explicitly selected/user-owned configuration can launch a subprocess.
    # Ambient repository files must not change the executable or capture destination.
    path = Path(os.environ.get("TAU_BASIC_MEMORY_CONFIG", "~/.tau/basic-memory.json")).expanduser()
    return Settings.model_validate_json(path.read_text()) if path.exists() else Settings()


@asynccontextmanager
async def connect(settings: Settings) -> AsyncIterator[ClientSession]:
    # Preserve BM's configured environment/routing, not Tau's Python import paths.
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    params = StdioServerParameters(command=settings.command, args=settings.args, env=env)
    # MCP requires a synchronous file descriptor; opening the null device does no disk I/O.
    with open(os.devnull, "w") as errlog:  # noqa: ASYNC230
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), settings.timeout_seconds)
                yield session


async def list_tools(session: ClientSession) -> list[Tool]:
    tools: list[Tool] = []
    cursors: set[str] = set()
    names: set[str] = set()
    cursor: str | None = None
    while True:
        page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
        for tool in page.tools:
            if tool.name in names:
                raise ValueError(f"MCP server advertised duplicate tool: {tool.name}")
            names.add(tool.name)
            tools.append(tool)
        cursor = page.next_cursor
        if cursor is None:
            return tools
        if cursor in cursors:
            raise ValueError("MCP tools/list repeated a pagination cursor")
        cursors.add(cursor)


async def discover(settings: Settings) -> list[Tool]:
    async with connect(settings) as session:
        return await asyncio.wait_for(list_tools(session), settings.timeout_seconds)


def discover_sync(settings: Settings) -> list[Tool]:
    # Tau composes tools immediately after synchronous setup, before session_start.
    # A temporary loop in a joined thread performs discovery and closes its child
    # before setup returns. No long-lived resource can leak if staged loading fails.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="bm-discovery") as executor:
        return executor.submit(lambda: asyncio.run(discover(settings))).result()


class McpConnection:
    """Own a persistent session separately from the tasks making tool calls."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session: ClientSession | None = None
        self.owner: asyncio.Task[None] | None = None
        self.stop_requested = asyncio.Event()
        self.active_calls: set[asyncio.Task[CallToolResult]] = set()

    async def start(self) -> None:
        if self.owner is not None:
            raise RuntimeError("Basic Memory connection already started")
        self.stop_requested = asyncio.Event()
        ready = asyncio.Future[None]()
        self.owner = asyncio.create_task(self._own_session(ready), name="basic-memory-mcp")
        try:
            await ready
        except BaseException:
            await self.close()
            raise

    async def _own_session(self, ready: asyncio.Future[None]) -> None:
        try:
            async with connect(self.settings) as session:
                self.session = session
                if not ready.done():
                    ready.set_result(None)
                await self.stop_requested.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(
                    RuntimeError(f"Basic Memory startup failed ({type(exc).__name__})")
                )
            else:
                raise
        finally:
            self.session = None

    async def close(self) -> None:
        self.stop_requested.set()
        calls = tuple(self.active_calls)
        for call in calls:
            call.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
        if self.owner is not None:
            if self.session is None and not self.owner.done():
                self.owner.cancel()
            try:
                await self.owner
            except asyncio.CancelledError:
                # Startup cancellation intentionally retires the owner before it is ready.
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling():
                    raise
            finally:
                self.owner = None

    async def call(self, name: str, arguments: dict[str, JsonValue]) -> CallToolResult:
        if self.session is None or self.stop_requested.is_set():
            raise RuntimeError("Basic Memory is disconnected; check configuration and /reload")
        call = asyncio.create_task(self.session.call_tool(name, arguments))
        self.active_calls.add(call)
        try:
            return await asyncio.wait_for(call, self.settings.timeout_seconds)
        finally:
            self.active_calls.discard(call)
