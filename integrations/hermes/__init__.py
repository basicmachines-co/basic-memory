"""
basic-memory — Hermes Memory Provider plugin

Wraps the basic-memory MCP server (`bm mcp`) to provide knowledge-graph-backed
memory for Hermes. Analog of openclaw-basic-memory.

Architecture:
- `_BmMcpActor` owns a long-lived asyncio loop in a daemon thread that holds
  the MCP `ClientSession` open across the agent's lifetime. Sync hooks dispatch
  through `asyncio.run_coroutine_threadsafe`.
- `BasicMemoryProvider` implements Hermes's `MemoryProvider` ABC: tools,
  prefetch, sync_turn (per-turn capture), on_session_end (summary).

The plugin loader text-greps for `register_memory_provider` or `MemoryProvider`
to detect this file as a memory provider — both tokens are present below.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any, Callable

# Hermes ABC + helpers — these resolve because Hermes adds its tree to sys.path
# when loading plugins (same pattern as plugins/memory/mem0/__init__.py:21).
from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

__version__ = "0.3.2"

logger = logging.getLogger("hermes.memory.basic-memory")


# ---------------------------------------------------------------------------
# MCP SDK import — soft. If unavailable, is_available() returns False.
# ---------------------------------------------------------------------------

_MCP_AVAILABLE = False
_MCP_IMPORT_ERROR: BaseException | None = None
try:
    from mcp import ClientSession, StdioServerParameters  # type: ignore
    from mcp.client.stdio import stdio_client  # type: ignore

    _MCP_AVAILABLE = True
except Exception as _e:  # pragma: no cover
    _MCP_IMPORT_ERROR = _e


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROVIDER_NAME = "basic-memory"

# Hermes-side tool names → BM MCP tool names. Curated subset of BM's surface.
_HERMES_TO_BM: dict[str, str] = {
    "bm_search": "search_notes",
    "bm_read": "read_note",
    "bm_write": "write_note",
    "bm_edit": "edit_note",
    "bm_context": "build_context",
    "bm_delete": "delete_note",
    "bm_move": "move_note",
    "bm_recent": "recent_activity",
    "bm_projects": "list_memory_projects",
    "bm_workspaces": "list_workspaces",
}

# Discovery tools that operate across all projects/workspaces. They don't
# accept project/project_id args (no per-call routing) and the user-facing
# schemas omit those properties.
_GLOBAL_TOOLS: frozenset = frozenset({"bm_projects", "bm_workspaces"})

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "bm_search",
        "description": (
            "Search the Basic Memory knowledge graph for notes, decisions, observations. "
            "Use BEFORE answering questions about prior work — context may already exist."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms (semantic + full-text)."},
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "bm_read",
        "description": "Read a specific note by title, permalink, or memory:// URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Note title, permalink, or memory:// URL.",
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "bm_write",
        "description": (
            "Create a new note in the knowledge graph. Use clear titles and a folder "
            "(e.g. 'projects', 'decisions', 'meetings')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string", "description": "Markdown body."},
                "folder": {"type": "string", "description": "Folder path within the project."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags.",
                },
            },
            "required": ["title", "content", "folder"],
        },
    },
    {
        "name": "bm_edit",
        "description": (
            "Edit an existing note. Operations: append, prepend, find_replace, replace_section. "
            "find_replace requires find_text. replace_section requires section."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": ["append", "prepend", "find_replace", "replace_section"],
                },
                "content": {"type": "string"},
                "find_text": {"type": "string", "description": "Required for find_replace."},
                "section": {"type": "string", "description": "Required for replace_section."},
            },
            "required": ["identifier", "operation", "content"],
        },
    },
    {
        "name": "bm_context",
        "description": (
            "Navigate the knowledge graph from a memory:// URL or note identifier. "
            "Returns the target note plus related notes via traversed relations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "memory:// URL or note identifier."},
                "depth": {
                    "type": "integer",
                    "description": "Relation traversal depth (default 1).",
                    "default": 1,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "bm_delete",
        "description": "Delete a note from the knowledge graph.",
        "parameters": {
            "type": "object",
            "properties": {"identifier": {"type": "string"}},
            "required": ["identifier"],
        },
    },
    {
        "name": "bm_move",
        "description": "Move a note to a different folder.",
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string"},
                "new_folder": {"type": "string"},
            },
            "required": ["identifier", "new_folder"],
        },
    },
    {
        "name": "bm_recent",
        "description": (
            "List notes updated recently. Use to surface what's been touched "
            "without a specific search query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timeframe": {
                    "type": "string",
                    "description": "Lookback window. Accepts '7d', '2 weeks', 'yesterday', etc.",
                    "default": "7d",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10).",
                    "default": 10,
                },
                "type": {
                    "type": "string",
                    "description": "Optional filter by item type (e.g. 'entity', 'observation').",
                },
            },
        },
    },
    {
        "name": "bm_projects",
        "description": (
            "List all available Basic Memory projects (local + cloud). Returns "
            "JSON with name and `external_id` (UUID) per project. Use the UUID "
            "as `project_id` on other bm_* tools for unambiguous routing across "
            "cloud workspaces. Call this when the user names a project that "
            "isn't the active one, or when you need to disambiguate same-name "
            "projects."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "bm_workspaces",
        "description": (
            "List Basic Memory Cloud workspaces the user belongs to. Workspaces "
            "are a BM Cloud concept; local mode returns just the personal "
            "workspace. Returns JSON with name, type, role, and default flag. "
            "Pair with bm_projects to disambiguate when the same project name "
            "exists in multiple workspaces."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


# Per-call project routing. Every bm_* tool accepts these — the agent overrides
# Hermes's configured project to read/write against a different Basic Memory
# project (e.g. a personal "main" project on BM Cloud). project_id is the
# UUID-based unambiguous form: required when the same project name exists in
# multiple cloud workspaces. _translate_args sends only one of the two to BM,
# with project_id winning when both are passed.
_PROJECT_ROUTING_PROPS: dict[str, dict[str, Any]] = {
    "project": {
        "type": "string",
        "description": (
            "Optional. Override the active Basic Memory project (e.g. 'main'). "
            "If the same project name exists in multiple cloud workspaces, "
            "use project_id instead for unambiguous routing."
        ),
    },
    "project_id": {
        "type": "string",
        "description": (
            "Optional. Override by project UUID (external_id from bm_projects). "
            "Disambiguates when a project name appears in multiple workspaces. "
            "Takes precedence over `project` if both are supplied."
        ),
    },
}

for _schema in TOOL_SCHEMAS:
    if _schema["name"] in _GLOBAL_TOOLS:
        # Discovery tools (bm_projects, bm_workspaces) list everything —
        # they don't take per-call routing.
        continue
    _schema["parameters"]["properties"].update(_PROJECT_ROUTING_PROPS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bm_binary_path() -> str | None:
    """Find the bm CLI without making network calls. Used by is_available()."""
    candidates = [
        os.path.expanduser("~/.local/bin/bm"),
        "/opt/homebrew/bin/bm",
        "/usr/local/bin/bm",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return which("bm")


def _uv_binary_path() -> str | None:
    """Find the uv CLI. Used to bootstrap-install basic-memory when bm is missing."""
    candidates = [
        os.path.expanduser("~/.local/bin/uv"),
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return which("uv")


def _install_bm_via_uv(timeout: float = 180.0) -> str | None:
    """
    Bootstrap-install basic-memory via `uv tool install`.

    Idempotent — re-runs are no-ops when the tool is already installed, so this
    converges with later manual `uv tool install basic-memory` calls and avoids
    the two-installations-sharing-one-config-dir foot-gun.

    Returns the resolved bm path on success, or None if uv is unavailable or
    the install failed.
    """
    uv = _uv_binary_path()
    if not uv:
        return None
    try:
        result = subprocess.run(
            [uv, "tool", "install", "basic-memory", "--quiet"],
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("basic-memory: `uv tool install basic-memory` failed: %s", e)
        return None
    if result.returncode != 0:
        # uv prints to stderr; capture the tail so the operator can debug.
        stderr_tail = (result.stderr or b"").decode("utf-8", errors="replace")[-400:]
        logger.warning(
            "basic-memory: `uv tool install basic-memory` exited %s: %s",
            result.returncode,
            stderr_tail.strip(),
        )
        return None
    return _bm_binary_path()


def _hostname() -> str:
    return socket.gethostname().split(".")[0].lower().replace(" ", "-")


def _default_project() -> str:
    # Each machine gets its own local project with this name. Cloud setups
    # use a different name (e.g. hermes-memory-cloud) so the two don't
    # collide in BM's per-workspace project registry.
    return "hermes-memory"


def _default_project_path() -> str:
    # ~/.basic-memory/ is reserved for BM's own application state; user
    # project files live in user space, parallel to ~/basic-memory/.
    return os.path.expanduser("~/hermes-memory/")


def _config_path(hermes_home: str) -> Path:
    return Path(hermes_home) / "basic-memory.json"


def _bm_config_path() -> Path:
    """Location of bm's own project registry."""
    return Path.home() / ".basic-memory" / "config.json"


def _bm_known_projects() -> dict[str, Any] | None:
    """
    Read bm's project registry. Returns None if the file is absent or
    unparseable — callers should treat that as "can't prove anything"
    rather than "project is missing".
    """
    path = _bm_config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    projects = data.get("projects")
    return projects if isinstance(projects, dict) else None


def _load_config(hermes_home: str) -> dict[str, Any]:
    p = _config_path(hermes_home)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        logger.warning("could not parse %s: %s — using defaults", p, e)
        return {}


def _truncate(s: Any, n: int) -> str:
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def _join_message_content(parts: Any) -> str:
    if isinstance(parts, str):
        return parts
    if isinstance(parts, list):
        out: list[str] = []
        for p in parts:
            if isinstance(p, dict):
                t = p.get("text") or p.get("content")
                if isinstance(t, str):
                    out.append(t)
            elif isinstance(p, str):
                out.append(p)
        return "\n".join(out)
    return str(parts) if parts is not None else ""


def _coerce_bool(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.lower() in ("true", "1", "yes", "y"):
            return True
        if v.lower() in ("false", "0", "no", "n"):
            return False
    return v


def _extract_mcp_text(result: Any) -> str:
    """
    Extract text from an MCP CallToolResult.

    Returns a JSON string for the agent. If the result is itself JSON, returns it
    as-is. Otherwise wraps the text in `{"text": "..."}` for downstream parsing.
    """
    is_error = bool(getattr(result, "isError", False))
    parts: list[str] = []
    for c in getattr(result, "content", None) or []:
        text = getattr(c, "text", None)
        if isinstance(text, str):
            parts.append(text)
    text = "\n".join(parts).strip()
    if is_error:
        return tool_error(text or "MCP tool returned error")
    if not text:
        return json.dumps({"ok": True})
    # If it's already JSON, pass through verbatim
    try:
        json.loads(text)
        return text
    except Exception:
        return json.dumps({"text": text})


_PERMALINK_JSON_RE = re.compile(r'"permalink"\s*:\s*"([^"]+)"')
_PERMALINK_MD_RE = re.compile(r"^\s*permalink\s*:\s*(\S+)\s*$", re.MULTILINE)


def _extract_permalink(text: str, fallback: str) -> str:
    """
    Extract a note permalink from any plausible BM response shape:

    1. Bare JSON dict with `permalink` key (output_format=json path)
    2. `{"text": "..."}` wrapping inner JSON or markdown
    3. Raw markdown response text (output_format=text default)

    Falls back to the supplied fallback when nothing matches.
    """
    if not isinstance(text, str) or not text:
        return fallback

    # Strategy 1: parse outer as JSON
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            if isinstance(d.get("permalink"), str):
                return d["permalink"]
            inner = d.get("text")
            if isinstance(inner, str):
                # Strategy 2: inner is JSON
                try:
                    d2 = json.loads(inner)
                    if isinstance(d2, dict) and isinstance(d2.get("permalink"), str):
                        return d2["permalink"]
                except Exception:
                    pass
                # Strategy 3a: inner is markdown with `permalink: ...` line
                m = _PERMALINK_MD_RE.search(inner)
                if m:
                    return m.group(1).rstrip(",.;")
                # Strategy 3b: inner contains JSON substring with permalink
                m = _PERMALINK_JSON_RE.search(inner)
                if m:
                    return m.group(1)
    except Exception:
        pass

    # Strategy 4: best-effort regex on raw text (covers exotic shapes)
    m = _PERMALINK_JSON_RE.search(text)
    if m:
        return m.group(1)
    m = _PERMALINK_MD_RE.search(text)
    if m:
        return m.group(1).rstrip(",.;")
    return fallback


# ---------------------------------------------------------------------------
# MCP actor — single asyncio loop in a daemon thread, owns ClientSession
# ---------------------------------------------------------------------------


class _BmMcpActor:
    """
    Owns the lifetime of one MCP ClientSession to the bm MCP server.

    Why this exists: Hermes calls memory provider hooks synchronously from
    a sync code path (memory_manager.py invokes provider.sync_turn / .prefetch
    / .handle_tool_call directly). MCP `ClientSession` is asyncio-bound and
    not thread-safe across event loops. So we run one asyncio loop in a
    daemon thread and ferry calls in via run_coroutine_threadsafe.
    """

    def __init__(self, server_argv: list[str], env: dict[str, str] | None = None):
        self._server_argv = list(server_argv)
        self._env = dict(env) if env is not None else os.environ.copy()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: "ClientSession" | None = None
        self._ready = threading.Event()
        self._init_error: BaseException | None = None
        self._stop_future: asyncio.Future | None = None
        self._tools_cache: list[dict[str, Any]] = []
        self._running = False

    def start(self, timeout: float = 25.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="bm-mcp-actor")
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            self._running = False
            raise TimeoutError(f"basic-memory MCP server didn't initialize within {timeout}s")
        if self._init_error is not None:
            self._running = False
            raise RuntimeError(f"basic-memory MCP server failed to start: {self._init_error}")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        except BaseException as e:
            if self._init_error is None:
                self._init_error = e
            self._ready.set()
            logger.exception("basic-memory MCP actor terminated with error")
        finally:
            self._running = False
            try:
                loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        params = StdioServerParameters(
            command=self._server_argv[0],
            args=self._server_argv[1:],
            env=self._env,
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._stop_future = asyncio.get_running_loop().create_future()
                    try:
                        listing = await session.list_tools()
                        self._tools_cache = [
                            {
                                "name": getattr(t, "name", ""),
                                "description": getattr(t, "description", "") or "",
                            }
                            for t in getattr(listing, "tools", []) or []
                        ]
                    except Exception as e:
                        logger.warning("list_tools failed: %s", e)
                        self._tools_cache = []
                    self._ready.set()
                    await self._stop_future  # blocks until shutdown
        except BaseException as e:
            if self._init_error is None:
                self._init_error = e
            self._ready.set()
            raise

    def call(self, tool_name: str, arguments: dict[str, Any], timeout: float = 30.0) -> str:
        if not self._running:
            raise RuntimeError("basic-memory MCP actor not running")
        if self._loop is None or self._session is None:
            raise RuntimeError("basic-memory MCP actor not started")
        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(tool_name, arguments),
            self._loop,
        )
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # Cancel the coroutine on the actor loop so we don't leak
            # a stuck call_tool. cancel() on a run_coroutine_threadsafe
            # future propagates cancellation into the wrapped coroutine.
            future.cancel()
            raise
        return _extract_mcp_text(result)

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self._tools_cache)

    def shutdown(self, timeout: float = 5.0) -> None:
        self._running = False
        if self._loop is not None and self._stop_future is not None:
            try:
                self._loop.call_soon_threadsafe(
                    lambda: (
                        (self._stop_future and not self._stop_future.done())
                        and self._stop_future.set_result(None)
                    )
                )
            except Exception:
                # Loop may already be closed; safe to ignore.
                pass
        if self._thread is not None:
            try:
                self._thread.join(timeout=timeout)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Argument translation: Hermes-side tool args → BM MCP tool args
# ---------------------------------------------------------------------------

_WORKSPACE_HASH_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-[0-9a-f]{32}$")


def _strip_memory_url_prefix(value: str) -> str:
    if value.startswith("memory://"):
        return value[len("memory://") :]
    return value


def _looks_workspace_qualified(value: str) -> bool:
    """Return True for BM Cloud workspace-qualified identifiers.

    Hermes normally injects its configured default project so calls operate on
    the provider's project instead of Basic Memory's process default. But BM
    Cloud routes fully-qualified identifiers itself; adding a default local
    project makes `personal/main/...` resolve under that local project instead.
    """
    path = _strip_memory_url_prefix(value).strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 3:
        return False

    workspace_slug = parts[0]
    return workspace_slug == "personal" or bool(_WORKSPACE_HASH_SLUG_RE.match(workspace_slug))


def _should_omit_default_project(hermes_tool: str, args: dict[str, Any]) -> bool:
    if hermes_tool in {"bm_read", "bm_edit", "bm_delete", "bm_move"}:
        identifier = args.get("identifier")
        return isinstance(identifier, str) and _looks_workspace_qualified(identifier)
    if hermes_tool == "bm_context":
        url = args.get("url")
        return isinstance(url, str) and _looks_workspace_qualified(url)
    return False


def _translate_args(
    hermes_tool: str, args: dict[str, Any], default_project: str
) -> tuple[str, dict[str, Any]]:
    bm_tool = _HERMES_TO_BM[hermes_tool]
    out: dict[str, Any] = {}

    # Project routing: project_id > project > configured default.
    # The agent passes one of these to operate on a project other than the
    # one Hermes is configured for. project_id (UUID from bm_projects) is
    # the unambiguous form across cloud workspaces — preferred when project
    # names might collide between workspaces. Only one of the two reaches
    # BM so server-side precedence rules don't enter the picture.
    #
    # Global discovery tools (bm_projects, bm_workspaces) list everything
    # and don't take routing args at all — skip the block for them.
    if hermes_tool not in _GLOBAL_TOOLS:
        project_id_override = args.get("project_id")
        project_name_override = args.get("project")
        if project_id_override:
            out["project_id"] = str(project_id_override)
        elif project_name_override:
            out["project"] = str(project_name_override)
        elif not _should_omit_default_project(hermes_tool, args):
            out["project"] = default_project

    if hermes_tool == "bm_search":
        out["query"] = args["query"]
        if "limit" in args and args["limit"] is not None:
            out["page_size"] = int(args["limit"])
    elif hermes_tool == "bm_read":
        out["identifier"] = args["identifier"]
    elif hermes_tool == "bm_write":
        out["title"] = args["title"]
        out["content"] = args["content"]
        out["directory"] = args["folder"]
        if args.get("tags"):
            out["tags"] = list(args["tags"])
    elif hermes_tool == "bm_edit":
        out["identifier"] = args["identifier"]
        out["operation"] = args["operation"]
        out["content"] = args["content"]
        if args.get("find_text") is not None:
            out["find_text"] = args["find_text"]
        if args.get("section") is not None:
            out["section"] = args["section"]
    elif hermes_tool == "bm_context":
        out["url"] = args["url"]
        if args.get("depth") is not None:
            out["depth"] = int(args["depth"])
    elif hermes_tool == "bm_delete":
        out["identifier"] = args["identifier"]
    elif hermes_tool == "bm_move":
        out["identifier"] = args["identifier"]
        out["destination_folder"] = args["new_folder"]
    elif hermes_tool == "bm_recent":
        if args.get("timeframe"):
            out["timeframe"] = str(args["timeframe"])
        if args.get("limit") is not None:
            out["page_size"] = int(args["limit"])
        if args.get("type"):
            out["type"] = args["type"]
    elif hermes_tool in _GLOBAL_TOOLS:
        # The agent needs to parse identifiers (UUIDs, workspace slugs) out
        # of the response, so request JSON regardless of BM's text default.
        out["output_format"] = "json"
    return bm_tool, out


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class BasicMemoryProvider(MemoryProvider):
    """Hermes Memory Provider backed by the basic-memory MCP server."""

    def __init__(self) -> None:
        self._actor: _BmMcpActor | None = None
        self._project: str = _default_project()
        self._mode: str = "local"
        self._project_path: str = _default_project_path()
        self._capture_per_turn: bool = True
        self._capture_session_end: bool = True
        self._capture_folder: str = "hermes-sessions"
        self._remember_folder: str = "bm-remember"
        self._session_id: str = ""
        self._hermes_home: str = ""
        self._session_note_id: str | None = None
        self._session_started_at: datetime | None = None
        self._sync_thread: threading.Thread | None = None
        self._prefetch_thread: threading.Thread | None = None
        self._prefetch_lock = threading.Lock()
        self._pending_prefetch: str = ""
        self._failure_count: int = 0
        self._failure_pause_until: float = 0.0
        self._initialized: bool = False
        self._first_user_msg: str | None = None

    # ---- Identity ----
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def is_available(self) -> bool:
        # Discovery hot path. NEVER make network calls or spawn subprocesses here.
        # We report available when either bm is present already OR uv is present
        # (we bootstrap-install bm via `uv tool install` at initialize() time).
        if not _MCP_AVAILABLE:
            return False
        if _bm_binary_path():
            return True
        if _uv_binary_path():
            return True
        return False

    # ---- Lifecycle ----
    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id or ""
        self._hermes_home = kwargs.get("hermes_home") or os.path.expanduser("~/.hermes")
        cfg = _load_config(self._hermes_home)
        self._mode = cfg.get("mode") or "local"
        self._project = cfg.get("project") or _default_project()
        self._project_path = os.path.expanduser(cfg.get("project_path") or _default_project_path())
        self._capture_per_turn = bool(_coerce_bool(cfg.get("capture_per_turn", True)))
        self._capture_session_end = bool(_coerce_bool(cfg.get("capture_session_end", True)))
        self._capture_folder = cfg.get("capture_folder") or "hermes-sessions"
        self._remember_folder = cfg.get("remember_folder") or "bm-remember"

        if not _MCP_AVAILABLE:
            logger.error(
                "basic-memory: MCP SDK unavailable; provider will not initialize: %s",
                _MCP_IMPORT_ERROR,
            )
            return

        # Bootstrap-install bm via uv if it's not already on disk. One-time cost
        # on a fresh machine; idempotent no-op once basic-memory is installed.
        if not _bm_binary_path():
            if _uv_binary_path() is None:
                logger.error(
                    "basic-memory: bm CLI not found and uv is not installed. "
                    "Install uv (https://docs.astral.sh/uv/) or run "
                    "`pip install basic-memory` manually. Provider will not initialize."
                )
                return
            logger.info(
                "basic-memory: bm CLI not found — installing basic-memory via "
                "`uv tool install` (one-time bootstrap)"
            )
            if _install_bm_via_uv() is None:
                logger.error(
                    "basic-memory: auto-install via uv failed. Run "
                    "`uv tool install basic-memory` manually to debug. "
                    "Provider will not initialize."
                )
                return

        if self._mode == "local":
            self._ensure_local_project()

        if not self._verify_project_registered():
            self._log_missing_project()
            return

        try:
            argv = self._server_argv()
        except Exception as e:
            logger.error("basic-memory: cannot determine server argv: %s", e)
            return

        actor = _BmMcpActor(argv)
        try:
            actor.start(timeout=25.0)
        except Exception as e:
            logger.error("basic-memory: MCP server failed to start: %s", e)
            return
        self._actor = actor

        tools = actor.list_tools()
        names = {t["name"] for t in tools}
        missing = [bm for bm in _HERMES_TO_BM.values() if bm not in names]
        if missing:
            logger.warning(
                "basic-memory: BM MCP missing expected tools: %s (got: %s)",
                missing,
                sorted(names),
            )

        self._session_started_at = datetime.now(timezone.utc)
        self._initialized = True
        logger.info(
            "basic-memory provider ready: mode=%s project=%s tools=%d",
            self._mode,
            self._project,
            len(tools),
        )

    def _ensure_local_project(self) -> None:
        bm = _bm_binary_path()
        if not bm:
            return
        os.makedirs(self._project_path, exist_ok=True)
        try:
            subprocess.run(
                [bm, "project", "add", self._project, self._project_path],
                check=False,
                capture_output=True,
                timeout=15,
            )
        except Exception as e:
            logger.debug("bm project add: %s", e)

    def _verify_project_registered(self) -> bool:
        """
        Confirm the configured project is registered with bm.

        Returns False only when we can prove the project is missing
        (bm config exists, parses, and the project name is absent).
        Otherwise returns True — including when bm's config doesn't exist
        yet — so we don't false-positive-reject on first-run setups.
        """
        projects = _bm_known_projects()
        if projects is None:
            return True
        return self._project in projects

    def _log_missing_project(self) -> None:
        if self._mode == "cloud":
            hint = f"`bm project add {self._project} --cloud` (optionally with --local-path) first"
        else:
            hint = f"`bm project add {self._project} {self._project_path}` first"
        logger.error(
            "basic-memory: project %r is not registered with bm. Run %s. "
            "Provider will not initialize.",
            self._project,
            hint,
        )

    def _server_argv(self) -> list[str]:
        bm = _bm_binary_path()
        if not bm:
            raise RuntimeError("bm CLI not found on PATH")
        # `bm mcp` works the same in local and cloud modes — bm reads
        # ~/.basic-memory/config.json to know whether the active project is local
        # or cloud-routed. We pass --project on each tool call.
        return [bm, "mcp"]

    def shutdown(self) -> None:
        if self._actor is not None:
            try:
                self._actor.shutdown(timeout=5.0)
            except Exception as e:
                logger.debug("actor shutdown: %s", e)
        self._actor = None
        self._initialized = False

    # ---- Tool surface ----
    def system_prompt_block(self) -> str:
        if not self._initialized:
            return ""
        return (
            "## Basic Memory Knowledge Graph\n"
            f"Active project: `{self._project}` ({self._mode}).\n"
            "\n"
            "**Use the `bm_*` tools below directly — do not shell out to the `bm` CLI.** "
            "These tools route through a persistent MCP connection "
            "(~0.1s/call); running `bm` from the shell spawns a fresh Python "
            "process per call (~1-2s) and bypasses Hermes's automatic per-turn "
            "capture.\n"
            "\n"
            "- `bm_search(query)` — call BEFORE answering about prior decisions, "
            "projects, meetings, or anything that might already be documented\n"
            "- `bm_read(identifier)` — fetch a note by title, permalink, or "
            "memory:// URL\n"
            "- `bm_context(url)` — navigate via memory:// URLs to find related "
            "notes\n"
            "- `bm_write(title, content, folder)` — capture decisions, insights, "
            "meeting outcomes worth preserving\n"
            "- `bm_edit(identifier, operation, content)` — append, prepend, "
            "find_replace, replace_section\n"
            "- `bm_delete(identifier)` / `bm_move(identifier, new_folder)` — "
            "maintenance\n"
            "- `bm_recent(timeframe)` — list notes updated within a window "
            "(default 7d) when there's no specific query yet\n"
            "- `bm_projects()` — list available projects (local + cloud) with "
            "their UUIDs; call when the user names a project that isn't the "
            "active one\n"
            "- `bm_workspaces()` — list BM Cloud workspaces; pair with "
            "`bm_projects` to disambiguate same-named projects\n"
            "\n"
            "**Cross-project routing.** Read/write tools accept optional `project` "
            "(name) or `project_id` (UUID). Omit both to use the active "
            f"project (`{self._project}`). Use `project_id` (from `bm_projects`) "
            "when the same project name exists in multiple cloud workspaces."
        )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        # Static. Hermes captures the schema list at register time (before
        # initialize() has run), so gating on `_initialized` would mean the
        # tools never make it into MemoryManager._tool_to_provider, and every
        # `bm_*` invocation returns "Unknown tool: bm_*" for the rest of the
        # session. handle_tool_call() does the runtime gate on `_initialized`
        # and returns a clean tool error if the actor isn't ready yet.
        return [dict(s) for s in TOOL_SCHEMAS]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if not self._initialized or self._actor is None:
            return tool_error("basic-memory provider not initialized")
        if tool_name not in _HERMES_TO_BM:
            return tool_error(f"Unknown tool: {tool_name}")
        try:
            bm_tool, bm_args = _translate_args(tool_name, args or {}, self._project)
        except KeyError as e:
            return tool_error(f"{tool_name}: missing required arg {e}")
        try:
            return self._actor.call(bm_tool, bm_args, timeout=30.0)
        except Exception as e:
            self._record_failure(e)
            logger.exception("bm tool call failed: %s", tool_name)
            return tool_error(f"{tool_name}: {e}")

    # ---- Recall (retrieve step) ----
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        with self._prefetch_lock:
            cached = self._pending_prefetch
            self._pending_prefetch = ""
        if cached:
            return cached
        if not self._initialized or self._actor is None or self._is_circuit_open():
            return ""
        try:
            # search_type="text" — bypass BM's "hybrid" default which mixes FTS
            # with vector search. Vector indexing is scheduled asynchronously
            # in BM (see services/search_service.py:_schedule_vector_sync_if_enabled),
            # so hybrid search can miss notes that were just written, especially
            # under cold-start or load. Prefetch is a recall hot path with a
            # 3s budget and the queries are usually keyword-like — FTS-only is
            # both faster and more deterministic.
            raw = self._actor.call(
                "search_notes",
                {
                    "project": self._project,
                    "query": query,
                    "page_size": 5,
                    "search_type": "text",
                    "output_format": "json",
                },
                timeout=3.0,
            )
            return self._format_prefetch(raw)
        except Exception as e:
            self._record_failure(e)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._initialized or self._actor is None or self._is_circuit_open():
            return
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            return

        def _bg() -> None:
            try:
                # search_type="text" mirrors prefetch() — see note there. The
                # background path can afford a longer timeout but the
                # async-vector-indexing race still applies.
                raw = self._actor.call(  # type: ignore[union-attr]
                    "search_notes",
                    {
                        "project": self._project,
                        "query": query,
                        "page_size": 5,
                        "search_type": "text",
                        "output_format": "json",
                    },
                    timeout=10.0,
                )
                with self._prefetch_lock:
                    self._pending_prefetch = self._format_prefetch(raw)
            except Exception as e:
                self._record_failure(e)
                logger.debug("queue_prefetch bg failed: %s", e)

        self._prefetch_thread = threading.Thread(target=_bg, daemon=True, name="bm-prefetch")
        self._prefetch_thread.start()

    def _format_prefetch(self, raw_json: str) -> str:
        try:
            data = json.loads(raw_json)
        except Exception:
            return ""
        # BM may return JSON directly or wrapped {"text": "..."}
        if isinstance(data, dict) and "text" in data and "results" not in data:
            try:
                data = json.loads(data["text"])
            except Exception:
                return ""
        results = data.get("results") if isinstance(data, dict) else None
        if not results:
            return ""
        lines = ["## Basic Memory Recall"]
        for r in list(results)[:5]:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "(untitled)")
            permalink = str(r.get("permalink") or "")
            preview_raw = r.get("content") or r.get("preview") or ""
            if not isinstance(preview_raw, str):
                preview_raw = str(preview_raw)
            preview = re.sub(r"\s+", " ", preview_raw)[:200]
            lines.append(f"- **{title}** (`{permalink}`) — {preview}")
        return "\n".join(lines)

    # ---- Per-turn capture (extract step) ----
    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if (
            not self._capture_per_turn
            or not self._initialized
            or self._actor is None
            or self._is_circuit_open()
        ):
            return
        if self._first_user_msg is None and user_content:
            self._first_user_msg = _truncate(user_content, 240)
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=3.0)

        def _bg() -> None:
            try:
                self._capture_turn(user_content, assistant_content)
            except Exception as e:
                self._record_failure(e)
                logger.warning("basic-memory sync_turn failed: %s", e)

        self._sync_thread = threading.Thread(target=_bg, daemon=True, name="bm-sync-turn")
        self._sync_thread.start()

    def _capture_turn(self, user: str, asst: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        block = (
            f"### {ts}\n\n"
            f"**User:** {_truncate(user, 4000)}\n\n"
            f"**Assistant:** {_truncate(asst, 4000)}\n"
        )
        if not self._session_note_id:
            title = self._session_note_title()
            content = (
                f"# {title}\n\n"
                f"Live transcript of Hermes session "
                f"`{self._session_id or '(no-id)'}`. "
                f"Auto-captured by basic-memory provider.\n\n"
                f"## Turns\n\n{block}\n"
            )
            args = {
                "project": self._project,
                "title": title,
                "directory": self._capture_folder,
                "content": content,
                "tags": ["hermes-session", _hostname()],
                "output_format": "json",
            }
            raw = self._actor.call("write_note", args, timeout=15.0)  # type: ignore[union-attr]
            self._session_note_id = _extract_permalink(raw, fallback=title)
        else:
            args = {
                "project": self._project,
                "identifier": self._session_note_id,
                "operation": "append",
                "content": "\n" + block,
            }
            self._actor.call("edit_note", args, timeout=15.0)  # type: ignore[union-attr]

    def _session_note_title(self) -> str:
        ts = (self._session_started_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H%M")
        # Hermes session IDs encode the date in the prefix (e.g. 20260510_080249_571920).
        # Use the trailing random component for disambiguation; falling back to seconds
        # only when there is no session id at all.
        sid = self._session_id or ""
        suffix = sid.rsplit("_", 1)[-1] if "_" in sid else sid[-6:]
        if not suffix:
            suffix = (self._session_started_at or datetime.now(timezone.utc)).strftime("%S")
        return f"Hermes Session {ts} {suffix}"

    # ---- Session-end summary ----
    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if not self._capture_session_end or not self._initialized or self._actor is None:
            return
        try:
            self._write_summary(messages)
        except Exception as e:
            logger.warning("basic-memory on_session_end summary failed: %s", e)

    def _write_summary(self, messages: list[dict[str, Any]]) -> None:
        user_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        asst_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"]
        first_user = self._first_user_msg or ""
        if not first_user and user_msgs:
            first_user = _truncate(_join_message_content(user_msgs[0].get("content")), 240)
        last_asst = ""
        if asst_msgs:
            last_asst = _truncate(_join_message_content(asst_msgs[-1].get("content")), 600)
        ts = (self._session_started_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H%M")
        sid = self._session_id or ""
        suffix = sid.rsplit("_", 1)[-1] if "_" in sid else sid[-6:]
        if not suffix:
            suffix = (self._session_started_at or datetime.now(timezone.utc)).strftime("%S")
        title = f"Hermes Session Summary {ts} {suffix}"
        lines = [
            f"# {title}",
            "",
            f"Session: `{self._session_id or '(no-id)'}`",
            f"Started: {ts}",
            f"Turns: {len(user_msgs)} user / {len(asst_msgs)} assistant",
            "",
            "## Opened with",
            "",
            first_user or "_(no user message)_",
            "",
            "## Last assistant turn",
            "",
            last_asst or "_(no assistant message)_",
            "",
            "## Notes",
            "",
            "_Auto-summary by basic-memory provider — refine as needed._",
            "",
        ]
        if self._session_note_id:
            lines += [
                "## Relations",
                "",
                f"- summary_of [[{self._session_note_id}]]",
                "",
            ]
        args = {
            "project": self._project,
            "title": title,
            "directory": self._capture_folder,
            "content": "\n".join(lines),
            "tags": ["hermes-session-summary", _hostname()],
            "output_format": "json",
        }
        try:
            self._actor.call("write_note", args, timeout=15.0)  # type: ignore[union-attr]
        except Exception as e:
            logger.warning("basic-memory: summary write failed: %s", e)

    # ---- Config wizard ----
    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "mode",
                "description": "Backend mode: local (writes to ~/.basic-memory/) or cloud",
                "default": "local",
                "choices": ["local", "cloud"],
            },
            {
                "key": "project",
                "description": "Basic Memory project name",
                "default": _default_project(),
            },
            {
                "key": "project_path",
                "description": "Filesystem path for the project (local mode)",
                "default": _default_project_path(),
            },
            {
                "key": "capture_per_turn",
                "description": "Auto-append every turn to a session transcript note",
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "capture_session_end",
                "description": "Write a session summary note at end of session",
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "capture_folder",
                "description": "BM folder where session notes land",
                "default": "hermes-sessions",
            },
            {
                "key": "remember_folder",
                "description": "BM folder where /bm-remember captures land",
                "default": "bm-remember",
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        coerced: dict[str, Any] = {k: _coerce_bool(v) for k, v in values.items()}
        path = _config_path(hermes_home)
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                pass
        existing.update(coerced)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2))

    # ---- Circuit breaker ----
    def _record_failure(self, exc: BaseException) -> None:
        self._failure_count += 1
        if self._failure_count >= 5 and self._failure_pause_until == 0.0:
            self._failure_pause_until = time.monotonic() + 120.0
            logger.warning(
                "basic-memory circuit open for 120s after %d failures (last: %s)",
                self._failure_count,
                exc,
            )

    def _is_circuit_open(self) -> bool:
        if self._failure_pause_until and time.monotonic() < self._failure_pause_until:
            return True
        if self._failure_pause_until and time.monotonic() >= self._failure_pause_until:
            self._failure_count = 0
            self._failure_pause_until = 0.0
        return False


# ---------------------------------------------------------------------------
# Slash commands — /bm-* surface for CLI/gateway sessions
# ---------------------------------------------------------------------------
#
# Plugin-owned slash commands let humans run BM operations without going
# through the agent. Handlers are sync `(raw_args: str) -> str` closures over
# a provider instance; output is printed verbatim by Hermes. We catch our own
# exceptions and return a plain-text message — Hermes also catches but yields
# a generic "Plugin command error: ..." line that hides detail from the user.


_SLASH_USAGE: dict[str, str] = {
    "bm-search": "Usage: /bm-search <query>\nSearch the Basic Memory knowledge graph.",
    "bm-read": "Usage: /bm-read <title|permalink|memory:// URL>",
    "bm-context": "Usage: /bm-context <identifier or memory:// URL>",
    "bm-recent": "Usage: /bm-recent [timeframe]   (default: 7d. Accepts '2 weeks', 'yesterday', etc.)",
    "bm-status": "Usage: /bm-status",
    "bm-remember": "Usage: /bm-remember <text>\nCapture a note. First line becomes the title.",
    "bm-project": "Usage: /bm-project   (lists Basic Memory projects; active one marked)",
    "bm-workspace": "Usage: /bm-workspace   (lists Basic Memory Cloud workspaces; cloud mode only)",
}


def _is_help_arg(raw_args: str) -> bool:
    s = raw_args.strip()
    return s in ("help", "-h", "--help", "?")


def _unwrap_json_or_text(raw: str) -> Any:
    """
    Best-effort decode of a tool result. Returns:
      - the inner JSON if `raw` parses to JSON (and unwraps {"text": "..."} when
        the inner string is also JSON)
      - the raw string otherwise
    """
    if not isinstance(raw, str) or not raw:
        return raw
    try:
        data = json.loads(raw)
    except Exception:
        return raw
    if isinstance(data, dict) and "text" in data and isinstance(data["text"], str):
        inner = data["text"]
        try:
            inner_data = json.loads(inner)
            return inner_data
        except Exception:
            return inner
    return data


def _format_result_rows(items: list[Any], header: str, empty_msg: str) -> str:
    if not items:
        return empty_msg
    lines = [header]
    for r in items:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or r.get("name") or "(untitled)")
        permalink = str(r.get("permalink") or r.get("path") or "")
        preview_raw = r.get("content") or r.get("preview") or r.get("snippet") or ""
        if not isinstance(preview_raw, str):
            preview_raw = str(preview_raw)
        preview = re.sub(r"\s+", " ", preview_raw)[:200].strip()
        bits = [f"- {title}"]
        if permalink:
            bits.append(f"({permalink})")
        if preview:
            bits.append(f"— {preview}")
        lines.append(" ".join(bits))
    return "\n".join(lines)


_slash_init_lock = threading.Lock()


def _slash_uninit(cmd: str) -> str:
    return f"{cmd}: basic-memory provider not initialized. Run `hermes memory status` to diagnose."


def _ensure_slash_ready(provider: "BasicMemoryProvider", cmd: str) -> str | None:
    """Lazily initialize the provider for human-invoked /bm-* commands.

    Hermes gateway command discovery can load memory-provider plugins before an
    agent session initializes memory. Slash handlers close over that discovered
    provider instance, so they need a small on-demand initialization path before
    touching the BM MCP actor.
    """
    if provider._initialized and provider._actor is not None:
        return None
    with _slash_init_lock:
        if provider._initialized and provider._actor is not None:
            return None
        try:
            provider.initialize(
                session_id=f"slash:{cmd}:{int(time.time())}",
                hermes_home=os.path.expanduser("~/.hermes"),
            )
        except Exception as e:
            logger.warning("basic-memory: slash init for /%s failed: %s", cmd, e)
            return f"{cmd}: failed to initialize basic-memory provider: {e}"
    if not provider._initialized or provider._actor is None:
        return _slash_uninit(cmd)
    return None


def _remember_title(text: str) -> str:
    """Derive a note title from free-form text. First non-empty line, ≤80 chars."""
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:80]
    # Fallback: timestamp
    return "Note " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H%M UTC")


def _build_slash_commands(
    provider: "BasicMemoryProvider",
) -> list[tuple[str, Callable[[str], str], str, str]]:
    """
    Return (name, handler, description, args_hint) tuples for each /bm-* command.
    Handlers close over `provider` to access the live actor and config.
    """

    def _bm_search(raw_args: str) -> str:
        args = raw_args.strip()
        if not args or _is_help_arg(args):
            return _SLASH_USAGE["bm-search"]
        if err := _ensure_slash_ready(provider, "bm-search"):
            return err
        try:
            raw = provider._actor.call(
                "search_notes",
                {
                    "project": provider._project,
                    "query": args,
                    "page_size": 10,
                    "output_format": "json",
                },
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-search: {e}"
        data = _unwrap_json_or_text(raw)
        results = data.get("results") if isinstance(data, dict) else None
        return _format_result_rows(
            list(results or []),
            header=f"Basic Memory results for {args!r}:",
            empty_msg=f"No results for {args!r}.",
        )

    def _bm_read(raw_args: str) -> str:
        args = raw_args.strip()
        if not args or _is_help_arg(args):
            return _SLASH_USAGE["bm-read"]
        if err := _ensure_slash_ready(provider, "bm-read"):
            return err
        try:
            raw = provider._actor.call(
                "read_note",
                {"project": provider._project, "identifier": args},
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-read: {e}"
        body = _unwrap_json_or_text(raw)
        if isinstance(body, dict) and "error" in body:
            return f"bm-read: {body['error']}"
        return body if isinstance(body, str) else json.dumps(body, indent=2)

    def _bm_context(raw_args: str) -> str:
        args = raw_args.strip()
        if not args or _is_help_arg(args):
            return _SLASH_USAGE["bm-context"]
        if err := _ensure_slash_ready(provider, "bm-context"):
            return err
        try:
            raw = provider._actor.call(
                "build_context",
                {"project": provider._project, "url": args, "depth": 1},
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-context: {e}"
        body = _unwrap_json_or_text(raw)
        if isinstance(body, dict) and "error" in body:
            return f"bm-context: {body['error']}"
        return body if isinstance(body, str) else json.dumps(body, indent=2)

    def _bm_recent(raw_args: str) -> str:
        args = raw_args.strip()
        if _is_help_arg(args):
            return _SLASH_USAGE["bm-recent"]
        timeframe = args or "7d"
        if err := _ensure_slash_ready(provider, "bm-recent"):
            return err
        try:
            raw = provider._actor.call(
                "recent_activity",
                {
                    "project": provider._project,
                    "timeframe": timeframe,
                    "page_size": 10,
                    "output_format": "json",
                },
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-recent: {e}"
        data = _unwrap_json_or_text(raw)
        results: list[Any] = []
        if isinstance(data, list):
            # BM's recent_activity returns `list[dict]` in JSON mode — that's
            # the documented signature (`-> str | list[dict]`).
            results = data
        elif isinstance(data, dict):
            # Older BM versions or wrapping layers may bury the rows under a key.
            for key in ("results", "items", "activity", "primary_results"):
                val = data.get(key)
                if isinstance(val, list):
                    results = val
                    break
        return _format_result_rows(
            results,
            header=f"Basic Memory activity ({timeframe}):",
            empty_msg=f"No activity in the last {timeframe}.",
        )

    def _bm_status(raw_args: str) -> str:
        if _is_help_arg(raw_args):
            return _SLASH_USAGE["bm-status"]
        lines = [
            "Basic Memory plugin status",
            f"  Provider:    {provider.name}",
            f"  Mode:        {provider._mode}",
            f"  Project:     {provider._project}",
        ]
        if provider._mode == "local":
            lines.append(f"  Path:        {provider._project_path}")
        bm_bin = _bm_binary_path()
        lines.append(f"  bm CLI:      {bm_bin or '(not found)'}")
        lines.append(f"  MCP module:  {'available' if _MCP_AVAILABLE else 'missing'}")
        lines.append(f"  Initialized: {'yes' if provider._initialized else 'no'}")
        lines.append(
            f"  Capture:     per-turn={provider._capture_per_turn}, "
            f"session-end={provider._capture_session_end}, "
            f"folder={provider._capture_folder!r}"
        )
        lines.append(f"  Remember folder: {provider._remember_folder!r}")
        if provider._failure_count:
            circuit = "open" if provider._is_circuit_open() else "closed"
            lines.append(f"  Failures:    {provider._failure_count} (circuit {circuit})")
        return "\n".join(lines)

    def _bm_remember(raw_args: str) -> str:
        text = raw_args.strip()
        if not text or _is_help_arg(text):
            return _SLASH_USAGE["bm-remember"]
        if err := _ensure_slash_ready(provider, "bm-remember"):
            return err
        title = _remember_title(text)
        folder = provider._remember_folder or "bm-remember"
        try:
            raw = provider._actor.call(
                "write_note",
                {
                    "project": provider._project,
                    "title": title,
                    "directory": folder,
                    "content": text,
                    "tags": ["manual-capture", _hostname()],
                    "output_format": "json",
                },
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-remember: {e}"
        permalink = _extract_permalink(raw, fallback=title)
        return f"Saved: {title}\n  Folder:    {folder}\n  Permalink: {permalink}"

    def _bm_project(raw_args: str) -> str:
        if _is_help_arg(raw_args):
            return _SLASH_USAGE["bm-project"]
        if err := _ensure_slash_ready(provider, "bm-project"):
            return err
        try:
            raw = provider._actor.call(
                "list_memory_projects",
                {"output_format": "json"},
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-project: {e}"
        data = _unwrap_json_or_text(raw)
        projects: list[Any] = []
        if isinstance(data, dict):
            for key in ("projects", "results", "items"):
                val = data.get(key)
                if isinstance(val, list):
                    projects = val
                    break
        if not projects and isinstance(data, list):
            projects = data
        if not projects:
            return "No Basic Memory projects found."
        lines = ["Basic Memory projects:"]
        for p in projects:
            if isinstance(p, dict):
                name = str(p.get("name") or p.get("permalink") or "(unnamed)")
                src = p.get("source") or p.get("workspace") or ""
            else:
                name, src = str(p), ""
            marker = "  (active)" if name == provider._project else ""
            tag = f" [{src}]" if src else ""
            lines.append(f"- {name}{tag}{marker}")
        return "\n".join(lines)

    def _bm_workspace(raw_args: str) -> str:
        if _is_help_arg(raw_args):
            return _SLASH_USAGE["bm-workspace"]
        if err := _ensure_slash_ready(provider, "bm-workspace"):
            return err
        if provider._mode != "cloud":
            return (
                "Workspaces are a Basic Memory Cloud concept. "
                f"This plugin is in '{provider._mode}' mode — no workspaces to list."
            )
        try:
            raw = provider._actor.call(
                "list_workspaces",
                {"output_format": "json"},
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-workspace: {e}"
        data = _unwrap_json_or_text(raw)
        workspaces: list[Any] = []
        if isinstance(data, dict):
            for key in ("workspaces", "results", "items"):
                val = data.get(key)
                if isinstance(val, list):
                    workspaces = val
                    break
        if not workspaces:
            return "No Basic Memory Cloud workspaces found."
        lines = ["Basic Memory Cloud workspaces:"]
        for w in workspaces:
            if isinstance(w, dict):
                name = str(w.get("name") or w.get("slug") or "(unnamed)")
                wtype = w.get("workspace_type") or ""
                role = w.get("role") or ""
                is_default = bool(w.get("is_default"))
            else:
                name, wtype, role, is_default = str(w), "", "", False
            bits = [f"- {name}"]
            tag_parts = [x for x in (wtype, role) if x]
            if tag_parts:
                bits.append(f"[{' / '.join(tag_parts)}]")
            if is_default:
                bits.append("(default)")
            lines.append(" ".join(bits))
        return "\n".join(lines)

    return [
        ("bm-search", _bm_search, "Search Basic Memory.", "<query>"),
        ("bm-read", _bm_read, "Read a Basic Memory note.", "<identifier>"),
        ("bm-context", _bm_context, "Show context graph for a Basic Memory note.", "<identifier>"),
        ("bm-recent", _bm_recent, "Show recent Basic Memory activity.", "[timeframe]"),
        ("bm-status", _bm_status, "Show the Basic Memory plugin status.", ""),
        ("bm-remember", _bm_remember, "Save a quick note to Basic Memory.", "<text>"),
        ("bm-project", _bm_project, "List Basic Memory projects.", ""),
        ("bm-workspace", _bm_workspace, "List Basic Memory Cloud workspaces.", ""),
    ]


# ---------------------------------------------------------------------------
# PluginManager reach-in — workaround for Hermes's memory-provider collector
# ---------------------------------------------------------------------------
#
# Hermes loads memory-provider plugins through a stripped-down `_ProviderCollector`
# context (plugins/memory/__init__.py) that only captures `register_memory_provider`;
# `register_command` and `register_skill` are not delegated. The result is that
# `ctx.register_command(...)` and `ctx.register_skill(...)` calls in this plugin
# silently no-op in real installs, even though Hermes's PluginManager *does*
# expose working slash-command and skill registries (used by general plugins).
#
# The clean fix lives upstream — a ~15-line patch to teach `_ProviderCollector`
# to delegate to PluginManager. Until that lands, we write to PluginManager's
# registries ourselves, matching exactly the entry shape and normalization
# `PluginContext.register_command` / `register_skill` produce. Idempotent with
# the future upstream fix: both code paths write identical entries to the same
# dicts.
#
# Recursion is safe: PluginManager.discover_and_load is idempotent
# (plugins.py:699) and explicitly skips memory-provider plugins at the
# manifest-routing stage (plugins.py:792-802), so calling
# `_ensure_plugins_discovered()` from inside our register() cannot re-enter us.

_PLUGIN_MANIFEST_NAME = "basic-memory"

_SKILL_DESCRIPTION = (
    "Reference for using bm_* tools and the Basic Memory knowledge graph "
    "(search-before-answer, capture decisions, navigate via memory:// URLs)."
)


def _register_via_plugin_manager(
    provider: "BasicMemoryProvider",
    skill_path: Path | None = None,
) -> None:
    """
    Reach into Hermes's PluginManager to register slash commands and the
    bundled skill, bypassing the memory-provider collector's no-op stubs.

    Best-effort: any failure (Hermes not on path, internal API renamed,
    discovery errored) logs at debug/warning and degrades to "no slash
    commands" rather than breaking memory-provider registration.
    """
    try:
        from hermes_cli.plugins import _ensure_plugins_discovered
    except Exception as e:
        logger.debug(
            "basic-memory: hermes_cli.plugins unavailable (%s); skipping slash-command reach-in",
            e,
        )
        return

    try:
        mgr = _ensure_plugins_discovered()
    except Exception as e:
        logger.warning("basic-memory: PluginManager discovery failed: %s", e)
        return

    # Mirror PluginContext.register_command's name-conflict guard against
    # built-in commands. Best-effort: if the import path changed, skip the
    # check rather than dropping every command.
    try:
        from hermes_cli.commands import resolve_command  # type: ignore
    except Exception:
        resolve_command = None  # type: ignore[assignment]

    plugin_commands = getattr(mgr, "_plugin_commands", None)
    if plugin_commands is None:
        logger.debug(
            "basic-memory: PluginManager has no _plugin_commands attr; slash commands skipped"
        )
    else:
        for name, handler, description, args_hint in _build_slash_commands(provider):
            # Mirror Hermes's normalization (plugins.py:426).
            clean = name.lower().strip().lstrip("/").replace(" ", "-")
            if not clean:
                continue
            if resolve_command is not None:
                try:
                    if resolve_command(clean) is not None:
                        logger.warning(
                            "basic-memory: skipping /%s — conflicts with a built-in command",
                            clean,
                        )
                        continue
                except Exception:
                    pass
            plugin_commands[clean] = {
                "handler": handler,
                "description": description or "Plugin command",
                "plugin": _PLUGIN_MANIFEST_NAME,
                "args_hint": (args_hint or "").strip(),
            }

    plugin_skills = getattr(mgr, "_plugin_skills", None)
    if plugin_skills is not None and skill_path is not None and skill_path.exists():
        plugin_skills[f"{_PLUGIN_MANIFEST_NAME}:basic-memory"] = {
            "path": skill_path,
            "plugin": _PLUGIN_MANIFEST_NAME,
            "bare_name": "basic-memory",
            "description": _SKILL_DESCRIPTION,
        }


# ---------------------------------------------------------------------------
# atexit safety net (mirrors plugins/memory/openviking pattern)
# ---------------------------------------------------------------------------

_active_providers: list[BasicMemoryProvider] = []


def _atexit_cleanup() -> None:
    for p in list(_active_providers):
        try:
            p.shutdown()
        except Exception:
            pass


atexit.register(_atexit_cleanup)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx: Any) -> None:
    """Register basic-memory as a memory provider plugin."""
    provider = BasicMemoryProvider()
    _active_providers.append(provider)
    ctx.register_memory_provider(provider)

    # Bundle the user-facing skill so `hermes plugins install` wires it up
    # with the rest of the plugin. The skill is opt-in via
    # `skill:view basic-memory:basic-memory` — it's not in the auto-loaded
    # `<available_skills>` index. Always-on agent guidance still flows
    # through `system_prompt_block()`.
    skill_path = Path(__file__).resolve().parent / "skill" / "SKILL.md"
    if skill_path.exists() and hasattr(ctx, "register_skill"):
        # Forward-compat: if Hermes's memory-provider collector ever delegates
        # register_skill to PluginManager (or another loader passes us a real
        # PluginContext), this lands the skill via the supported path. The
        # reach-in below covers the current production collector either way.
        try:
            ctx.register_skill(
                "basic-memory",
                skill_path,
                description=_SKILL_DESCRIPTION,
            )
        except Exception as e:
            logger.warning("basic-memory: register_skill failed: %s", e)

    # Forward-compat: when Hermes's memory-provider collector gains
    # register_command (PR to NousResearch/hermes-agent pending), this is the
    # right path. Until then, hasattr returns False and we fall through to
    # the reach-in below.
    if hasattr(ctx, "register_command"):
        for name, handler, description, args_hint in _build_slash_commands(provider):
            try:
                ctx.register_command(
                    name,
                    handler,
                    description=description,
                    args_hint=args_hint,
                )
            except Exception as e:
                logger.warning("basic-memory: register_command(%s) failed: %s", name, e)

    # Write directly to PluginManager's registries. This is the production
    # path today; see _register_via_plugin_manager docstring for the why.
    _register_via_plugin_manager(
        provider,
        skill_path=skill_path if skill_path.exists() else None,
    )
