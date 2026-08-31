"""Shared helpers for driving an external Basic Memory runtime.

Everything here talks to Basic Memory through its public contracts only — the
`bm` CLI and the `bm mcp` stdio server — never through internal imports, so the
same code runs unchanged against any BM version under comparison (installed
`bm`, or a checkout via ``uv run --project <path> basic-memory``).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Literal

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult, Tool

from basic_memory_benchmarks.utils import run_command

FALLBACK_SETTLE_SECONDS = 10.0


@dataclass
class _McpToolRequest:
    name: str
    arguments: dict[str, Any]
    response: Future[CallToolResult]


class WarmMcpClient:
    """One warm `bm mcp` stdio session, callable from any thread.

    The session runs on its own thread with its own subprocess; `call_tool`
    marshals requests through a queue so callers pay startup cost once per
    session instead of once per tool call. Requests are strictly one at a time
    per session — concurrency comes from running multiple sessions.
    """

    def __init__(
        self,
        *,
        command: str = "bm",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        startup_timeout_seconds: float = 30.0,
        request_timeout_seconds: float = 60.0,
        required_tool: str = "search_notes",
    ) -> None:
        self._command = command
        self._args = args or ["mcp"]
        self._env = env
        self._startup_timeout_seconds = startup_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._required_tool = required_tool
        self._requests: Queue[_McpToolRequest | None] = Queue()
        self._ready = threading.Event()
        self._startup_error: Exception | None = None
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._tools: list[Tool] = []

    async def _serve(self) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asyncio always supplies the current task
            raise RuntimeError("MCP session started without an asyncio task")
        with self._state_lock:
            self._loop = loop
            self._serve_task = task

        params = StdioServerParameters(command=self._command, args=self._args, env=self._env)
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                tool_names = {tool.name for tool in tools.tools}
                if self._required_tool not in tool_names:
                    raise RuntimeError(f"bm mcp server does not expose '{self._required_tool}'")

                # Captured before _ready so tools() is populated as soon as
                # start() returns; consumers use it for surface verification.
                self._tools = list(tools.tools)
                self._ready.set()

                while True:
                    request = await asyncio.to_thread(self._requests.get)
                    if request is None:
                        break
                    try:
                        result = await session.call_tool(request.name, request.arguments)
                    except asyncio.CancelledError:
                        if not request.response.done():
                            request.response.set_exception(
                                RuntimeError("bm mcp session stopped during tool call")
                            )
                        raise
                    except Exception as exc:
                        if not request.response.done():
                            request.response.set_exception(exc)
                    else:
                        if not request.response.done():
                            request.response.set_result(result)

    def _fail_pending_requests(self) -> None:
        while True:
            try:
                request = self._requests.get_nowait()
            except Empty:
                return
            if request is not None and not request.response.done():
                request.response.set_exception(RuntimeError("bm mcp session stopped"))

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._startup_error = exc
        finally:
            self._fail_pending_requests()
            with self._state_lock:
                self._loop = None
                self._serve_task = None
            self._ready.set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._thread_main,
            name="bm-benchmark-mcp-client",
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(timeout=self._startup_timeout_seconds):
            self.stop()
            raise TimeoutError("Timed out starting bm mcp session")
        if self._startup_error is not None:
            startup_error = self._startup_error
            self.stop()
            raise RuntimeError("Failed to start bm mcp session") from startup_error

    def tools(self) -> list[Tool]:
        """Tool definitions the server advertised at session start."""
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("bm mcp session is not running")
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("bm mcp session is not running")

        response: Future[CallToolResult] = Future()
        self._requests.put(_McpToolRequest(name=name, arguments=arguments, response=response))
        return response.result(timeout=self._request_timeout_seconds)

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return

        if thread.is_alive():
            # Trigger: stop can follow a timed-out tool call that is still running.
            # Why: queued shutdown alone cannot interrupt that call, so verification
            # could race an untracked writer. Outcome: task cancellation exits the
            # stdio context, whose MCP transport terminates the child process.
            self._requests.put(None)
            with self._state_lock:
                loop = self._loop
                serve_task = self._serve_task
            if loop is not None and serve_task is not None and loop.is_running():
                loop.call_soon_threadsafe(serve_task.cancel)
            thread.join(timeout=self._startup_timeout_seconds)
            if thread.is_alive():
                raise RuntimeError("bm mcp session did not stop before the shutdown deadline")

        self._thread = None


def resolve_bm_command_prefix(bm_local_path: str | None) -> list[str]:
    """Resolve how to invoke Basic Memory: installed `bm` or a local checkout."""
    if bm_local_path:
        local_path = Path(bm_local_path)
        if not local_path.exists():
            raise ValueError(f"--bm-local-path not found: {local_path}")
        return ["uv", "run", "--project", str(local_path), "basic-memory"]
    return ["bm"]


def status_json_is_ready(payload: dict[str, Any]) -> bool | None:
    """Interpret `bm status --json` output across BM versions.

    Returns ``True`` when a known schema is idle, ``False`` when it is busy,
    and ``None`` when the payload has no supported readiness signal.
    """
    total = payload.get("total")
    if isinstance(total, int):
        return total == 0

    recognized_signal = False
    for list_key in ("new", "modified", "deleted", "skipped_files"):
        value = payload.get(list_key)
        if isinstance(value, list):
            recognized_signal = True
            if value:
                return False

    for dict_key in ("moves", "checksums"):
        value = payload.get(dict_key)
        if isinstance(value, dict):
            recognized_signal = True
            if value:
                return False

    status = payload.get("status")
    if isinstance(status, str):
        lowered = status.lower()
        if "no changes" in lowered or "up to date" in lowered:
            return True
        if "sync" in lowered or "index" in lowered or "pending" in lowered:
            return False

    for key in ("is_syncing", "is_indexing", "sync_in_progress", "index_in_progress"):
        value = payload.get(key)
        if isinstance(value, bool):
            recognized_signal = True
            if value:
                return False

    for key in ("pending_files", "pending", "unindexed_files", "queued_files", "queue_size"):
        value = payload.get(key)
        if isinstance(value, int):
            recognized_signal = True
            if value != 0:
                return False

    return True if recognized_signal else None


def isolated_bm_env(home: Path) -> dict[str, str]:
    """Benchmark-owned env: fresh config dir AND fresh default-project home.

    BASIC_MEMORY_HOME points inside the sandbox so the auto-created default
    project indexes an empty directory instead of the operator's personal
    notes (which would add noise to settle timing and DB contents).
    """
    env = dict(os.environ)
    env.pop("BASIC_MEMORY_CLOUD_MODE", None)
    env["BASIC_MEMORY_CONFIG_DIR"] = str(home / "config")
    env["BASIC_MEMORY_HOME"] = str(home / "default-home")
    return env


def settle_index(
    *,
    prefix: list[str],
    env: dict[str, str],
    project_name: str,
    timeout_seconds: float,
) -> tuple[float, Literal["status-json", "fixed-delay"]]:
    """Wait until the index reports no pending work; returns (seconds, mode)."""
    start = time.monotonic()
    probe = run_command(prefix + ["status", "--json", "--local"], check=False, env=env)
    merged = ((probe.stdout or "") + "\n" + (probe.stderr or "")).lower()
    if "no such option: --json" in merged:
        # Old BM without --json: no readiness signal exists; give the watcher a
        # fixed grace period and record the mode so the artifact is explicit.
        time.sleep(FALLBACK_SETTLE_SECONDS)
        return time.monotonic() - start, "fixed-delay"

    deadline = start + timeout_seconds
    delay = 0.25
    while True:
        completed = run_command(
            prefix + ["status", "--project", project_name, "--json", "--local"], env=env
        )
        payload = json.loads(completed.stdout.strip() or "{}")
        if isinstance(payload, dict):
            readiness = status_json_is_ready(payload)
            if readiness is None:
                time.sleep(FALLBACK_SETTLE_SECONDS)
                return time.monotonic() - start, "fixed-delay"
            if readiness:
                return time.monotonic() - start, "status-json"
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Index did not settle within {timeout_seconds}s for project '{project_name}'"
            )
        time.sleep(delay)
        delay = min(delay * 2, 2.0)
