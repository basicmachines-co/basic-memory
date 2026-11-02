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
import signal
import socket
import subprocess
import threading
import time
import weakref
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any, Callable, NamedTuple

# Hermes ABC + helpers — these resolve because Hermes adds its tree to sys.path
# when loading plugins (same pattern as plugins/memory/mem0/__init__.py:21).
from agent.memory_provider import MemoryProvider, RecallStatus
from tools.registry import tool_error

# Constraint: Hermes v0.22 requires every memory-provider background job to run
# through `spawn_context_thread`, so the profile-scoped HERMES_HOME and secret
# scope follow the work onto the new thread. The supported floor (v0.20) has no
# such helper and no context-scoped profiles, where a plain thread is equivalent.
try:
    from agent.memory_provider import spawn_context_thread as _hermes_spawn_context_thread
except ImportError:
    _hermes_spawn_context_thread = None


def _spawn_thread(target: Callable[[], None], *, name: str) -> threading.Thread:
    """Start a daemon thread under the caller's Hermes profile context."""
    if _hermes_spawn_context_thread is not None:
        thread = _hermes_spawn_context_thread(target, name=name, daemon=True)
    else:
        thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread


__version__ = "1.0.0"

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
    "bm_search": "bm_search",
    "bm_read": "bm_read",
    "bm_write": "bm_write",
    "bm_edit": "bm_edit",
    "bm_context": "bm_context",
    "bm_delete": "bm_delete",
    "bm_move": "bm_move",
    "bm_recent": "bm_project",
    "bm_projects": "bm_project",
    "bm_workspaces": "bm_project",
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
            "Use it before answering questions about prior work, since the answer may "
            "already be documented. Returns ranked notes; follow up with bm_read for "
            "full content."
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
        "description": (
            "Read a specific note by title, permalink, or memory:// URL. Returns the "
            "full markdown body with observations and relations. To read back a note "
            "written to another project, pass the same `project` or `project_id` "
            "used for bm_write; otherwise the active project is used. Pass "
            "output_format 'json' to get the note's `checksum`, which bm_write "
            "accepts as `expected_checksum` to replace only the revision you read."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Note title, permalink, or memory:// URL.",
                },
                "include_frontmatter": {
                    "type": "boolean",
                    "description": (
                        "Return the raw note including its YAML frontmatter (default false)."
                    ),
                },
                "output_format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "description": (
                        "'text' (default) returns the markdown. 'json' returns title, "
                        "permalink, file_path, content, frontmatter, and checksum."
                    ),
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "bm_write",
        "description": (
            "Create a new note in the knowledge graph. Call this whenever the user "
            "asks to remember, record, save, or note something — acknowledging "
            "without this call saves nothing. The result returns the note's "
            "permalink: quote it when confirming the save, never a filename "
            "recalled from memory. Use clear titles and a folder "
            "(e.g. 'projects', 'decisions', 'meetings'). An existing note is "
            "replaced only with overwrite=true; add expected_checksum (from "
            "bm_read with output_format 'json') to replace only that revision."
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
                "note_type": {
                    "type": "string",
                    "description": "Note type stored in frontmatter `type:` (default 'note').",
                },
                "metadata": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "Extra frontmatter fields merged into the note. Nested "
                        "objects are supported."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "true replaces an existing note; false refuses when one "
                        "exists. Omitted, Basic Memory's write_note_overwrite_default "
                        "setting decides."
                    ),
                },
                "expected_checksum": {
                    "type": "string",
                    "description": (
                        "Requires overwrite=true. The checksum of the note as read; "
                        "the note is replaced only while it is still that revision."
                    ),
                },
            },
            "required": ["title", "content", "folder"],
        },
    },
    {
        "name": "bm_edit",
        "description": (
            "Edit an existing note. Operations: append, prepend, find_replace, replace_section. "
            "find_replace requires find_text. replace_section requires section. "
            "The result returns the note's permalink — quote it when confirming the edit."
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
        "description": (
            "Permanently delete a note: removes its markdown file and index entries. "
            "Use only when the user asks to remove a note; use bm_move to relocate "
            "and bm_edit to change content."
        ),
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
        "name": "bm_move",
        "description": (
            "Move a note to another folder in the same project. The content is "
            "preserved; only the location changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Note title, permalink, or memory:// URL.",
                },
                "new_folder": {
                    "type": "string",
                    "description": "Project-relative destination folder, e.g. 'archive/2026'.",
                },
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
            "JSON with name, `external_id` (UUID), `source` ('local', 'cloud', or "
            "'local+cloud'), and `qualified_name` per project. Use the UUID "
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
            "List projects with their Basic Memory Cloud workspace fields. "
            "Workspaces without accessible projects are not included. "
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


# Variables that point a child process at the *parent's* Python installation.
# Hermes ships its own interpreter (3.11 today) and exports these; `bm` is a
# uv-managed 3.12+ tool with its own environment. A child that inherits them
# resolves imports against Hermes's site-packages, which surfaces as a native
# extension ABI failure rather than a missing package.
#
# `__PYVENV_LAUNCHER__` is included defensively: the macOS framework launcher
# exports it to steer a child at a specific interpreter. We have not observed a
# failure caused by it, unlike the other three.
_PARENT_PYTHON_ENV_VARS = (
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "__PYVENV_LAUNCHER__",
)


def _clean_child_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """
    Copy of `env` (default: this process's environment) with the parent's
    Python-interpreter variables removed.

    Every external process this plugin spawns — `bm mcp`, `uv tool install`,
    `bm project add` — runs under its own interpreter and must resolve its own
    dependencies. Inheriting Hermes's `PYTHONPATH` makes it import Hermes's
    site-packages instead, reported as:

        ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'

    which is a compiled-extension mismatch between two Python versions, not a
    missing dependency — so it is invisible to a `pip install` fix.

    Everything else is preserved: the child still needs PATH, HOME, HTTP proxy
    settings and credentials. An explicitly supplied `env` is sanitized too,
    since the guarantee is about what the child receives, not about who
    assembled it.
    """
    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if k not in _PARENT_PYTHON_ENV_VARS}


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
    converges with later manual `uv tool install basic-memory --prerelease=allow`
    calls and avoids
    the two-installations-sharing-one-config-dir foot-gun.

    Returns the resolved bm path on success, or None if uv is unavailable or
    the install failed.
    """
    uv = _uv_binary_path()
    if not uv:
        return None
    try:
        result = subprocess.run(
            # basic-memory pins a FastMCP pre-release; without this flag uv
            # silently installs the last release that had none (#1338).
            [uv, "tool", "install", "basic-memory", "--prerelease=allow", "--quiet"],
            check=False,
            capture_output=True,
            timeout=timeout,
            env=_clean_child_env(),
        )
    except Exception as e:
        logger.warning(
            "basic-memory: `uv tool install basic-memory --prerelease=allow` failed: %s", e
        )
        return None
    if result.returncode != 0:
        # uv prints to stderr; capture the tail so the operator can debug.
        stderr_tail = (result.stderr or b"").decode("utf-8", errors="replace")[-400:]
        logger.warning(
            "basic-memory: `uv tool install basic-memory --prerelease=allow` exited %s: %s",
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


# `source` values in `bm_project list` rows that mean the project is registered
# in this machine's local config. A local project sharing a permalink with cloud
# projects is attached to one of them and listed as "local+cloud".
_LOCALLY_REGISTERED_SOURCES = frozenset({"local", "local+cloud"})


def _project_listed(projects: list[dict[str, Any]], project: str, *, local: bool) -> bool:
    """True when `bm_project list` rows include `project`.

    Accepts every spelling Basic Memory routes by: the name, the
    workspace-qualified name, or the external id. With `local`, only rows
    registered in the local config count.
    """
    wanted = project.casefold()
    for row in projects:
        # Trigger: Hermes runs in local mode and the only match is a cloud project.
        # Why: Basic Memory routes a name absent from local config to the cloud
        #   (BasicMemoryConfig.get_project_mode), so accepting that match would
        #   upload local-mode transcripts to the cloud project.
        # Outcome: the row does not count, and the caller creates the local project.
        if local and row["source"] not in _LOCALLY_REGISTERED_SOURCES:
            continue
        if row.get("external_id") == project:
            return True
        if wanted in {str(row.get(key) or "").casefold() for key in ("name", "qualified_name")}:
            return True
    return False


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


# Masks common credential forms in captured text. Mirrors integrations/tau/privacy.py;
# the plugin is installed and loaded as a single module, so it keeps its own copy.
_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.DOTALL),
    # Bearer values and assignments go before bare tokens, so a token inside them is
    # masked together with the rest of the value.
    re.compile(r"(?i)\bBearer\s+[^\s\"']+"),
    re.compile(
        r"""(?i)["']?\b(?:[A-Z0-9_]*(?:API_KEY|PASSWORD|SECRET|ACCESS_TOKEN))["']?\s*[:=]\s*"""
        r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;]+)"""
    ),
    re.compile(r"\b(?:sk-|bmc_|ghp_|github_pat_|hf_)[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)


def _public_text(s: Any) -> str:
    """Mask credentials before captured text is written to a note.

    Call this before _truncate: truncating first can cut off a private key's END
    line, and the rest of the key would no longer match.
    """
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    for pattern in _CREDENTIAL_PATTERNS:
        s = pattern.sub("[credential redacted]", s)
    return s


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


_SNIPPET_STOPWORDS = frozenset(
    "the and for with that this what when where which who why how did does was were "
    "are about from into have has had you your our they them their then than there "
    "can could would should will just also some any all not but".split()
)


def _snippet_around(text: str, query: str, width: int = 240) -> str:
    """One-line excerpt of `text` that starts just before the first query term it contains.

    Recall runs bm_search in text mode, which returns whole note content but no
    matched chunk; a note's opening lines (a transcript header) rarely show why
    it matched. Falls back to the opening when no term occurs.
    """
    flat = re.sub(r"\s+", " ", text or "").strip()
    terms = [
        t
        for t in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", (query or "").lower())
        if t not in _SNIPPET_STOPWORDS
    ]
    lowered = flat.lower()
    hits = [i for i in (lowered.find(t) for t in terms) if i >= 0]
    if not hits:
        return flat[:width]
    start = max(0, min(hits) - width // 6)
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


def _extract_mcp_text(result: Any) -> str:
    """
    Extract text from an MCP CallToolResult.

    Returns a JSON string for the agent. If the result is itself JSON, returns it
    as-is. Otherwise wraps the text in `{"text": "..."}` for downstream parsing.
    """
    # The `mcp` SDK is an unpinned dependency (see plugin.yaml), and its result
    # shapes (CallToolResult, content blocks) have shifted across versions — read
    # these fields defensively with getattr rather than direct attribute access.
    # mcp 2.x (pinned in plugin.yaml) names the flag `is_error`; the wire
    # field `isError` is not an attribute, so reading it would report every
    # refused write (note exists, revision conflict) to the agent as success.
    is_error = bool(getattr(result, "is_error", False))
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
        # Sanitized so `bm mcp` resolves imports against its own interpreter,
        # not Hermes's — see _clean_child_env.
        self._env = _clean_child_env(env)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: "ClientSession" | None = None
        self._ready = threading.Event()
        self._init_error: BaseException | None = None
        self._stop_future: asyncio.Future | None = None
        self._main_task: asyncio.Task[None] | None = None
        self._shutdown_requested = threading.Event()
        self._tools_cache: list[dict[str, Any]] = []
        self._running = False

    def start(self, timeout: float = 25.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._shutdown_requested.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="bm-mcp-actor")
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            # Trigger: startup timed out before _main created its stop future.
            # Why: joining alone cannot exit the stdio context or reap its child.
            # Outcome: cancel the actor task and wait for its async contexts to close.
            self.shutdown(timeout=5.0)
            raise TimeoutError(f"basic-memory MCP server didn't initialize within {timeout}s")
        if self._init_error is not None:
            self._running = False
            raise RuntimeError(f"basic-memory MCP server failed to start: {self._init_error}")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._main_task = loop.create_task(self._main())
            if self._shutdown_requested.is_set():
                self._main_task.cancel()
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            # Cancellation is the intentional pre-ready shutdown path. Wake a
            # concurrent start() without logging an expected cleanup as a crash.
            if not self._ready.is_set() and self._init_error is None:
                self._init_error = RuntimeError("basic-memory MCP actor stopped during startup")
            self._ready.set()
        except BaseException as e:
            if self._init_error is None:
                self._init_error = e
            self._ready.set()
            logger.exception("basic-memory MCP actor terminated with error")
        finally:
            self._running = False
            self._main_task = None
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
                        # Same unpinned-`mcp`-SDK reason as parse_result above:
                        # ListToolsResult / Tool field shapes vary across SDK versions,
                        # so read them defensively rather than by direct attribute access.
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

    def is_alive(self) -> bool:
        """True while the actor loop thread is up and accepting calls."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._running = False
        self._shutdown_requested.set()
        if self._loop is not None:
            try:

                def request_stop() -> None:
                    if not self._ready.is_set():
                        if self._main_task is not None and not self._main_task.done():
                            self._main_task.cancel()
                        return
                    if self._stop_future is not None and not self._stop_future.done():
                        self._stop_future.set_result(None)
                    elif self._main_task is not None and not self._main_task.done():
                        self._main_task.cancel()

                self._loop.call_soon_threadsafe(request_stop)
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
            out["project"] = str(project_id_override)
        elif project_name_override:
            out["project"] = str(project_name_override)
        elif not _should_omit_default_project(hermes_tool, args):
            out["project"] = default_project

    if hermes_tool == "bm_search":
        out["query"] = args["query"]
        if "limit" in args and args["limit"] is not None:
            out["page_size"] = int(args["limit"])
    elif hermes_tool == "bm_read":
        out["include_frontmatter"] = False
        out["identifier"] = args["identifier"]
        for key in ("include_frontmatter", "output_format"):
            if args.get(key) is not None:
                out[key] = args[key]
    elif hermes_tool == "bm_write":
        out["title"] = args["title"]
        out["content"] = args["content"]
        out["directory"] = args["folder"]
        if args.get("tags"):
            out["tags"] = list(args["tags"])
        # Forwarded as given so BM's own validation applies (e.g. it rejects
        # expected_checksum without overwrite=true). `is not None` keeps an
        # explicit overwrite=false, which must reach BM to refuse a replace.
        for key in ("note_type", "metadata", "overwrite", "expected_checksum"):
            if args.get(key) is not None:
                out[key] = args[key]
    elif hermes_tool == "bm_edit":
        out["identifier"] = args["identifier"]
        out["command"] = args["operation"]
        out["content"] = args["content"]
        if args.get("find_text") is not None:
            out["find_text"] = args["find_text"]
        if args.get("section") is not None:
            out["section"] = args["section"]
    elif hermes_tool == "bm_context":
        out["identifier"] = args["url"]
        if args.get("depth") is not None:
            out["depth"] = int(args["depth"])
    elif hermes_tool == "bm_delete":
        out["identifier"] = args["identifier"]
    elif hermes_tool == "bm_move":
        out["identifier"] = args["identifier"]
        out["destination"] = str(args["new_folder"]).rstrip("/") + "/"
    elif hermes_tool == "bm_recent":
        out["command"] = "recent"
        if args.get("timeframe"):
            out["since"] = str(args["timeframe"])
        if args.get("limit") is not None:
            out["page_size"] = int(args["limit"])
        if args.get("type"):
            out["items"] = ["note" if args["type"] == "entity" else args["type"]]
    elif hermes_tool in _GLOBAL_TOOLS:
        # The agent needs to parse identifiers (UUIDs, workspace slugs) out
        # of the response, so request JSON regardless of BM's text default.
        out["command"] = "list"
        out["output_format"] = "json"
    return bm_tool, out


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


# --- Live-provider registry ---
# Hermes v0.22 gives every agent its own provider instance, bound to one profile
# (HERMES_HOME) at initialize(). Human-invoked /bm-* commands run outside any
# agent, so they look up the initialized instance for the caller's profile here
# instead of closing over whichever (possibly never-initialized) instance
# register() created. Lookup only: an instance is never rebound to another
# profile. Weak references let Hermes drop an agent's provider normally.
_registry_lock = threading.Lock()
_live_providers: dict[str, weakref.ref[BasicMemoryProvider]] = {}

# Why the last initialize() for a profile failed, cleared when one succeeds.
# Keyed by profile like _live_providers, because a failed provider never
# registers there: without this, /bm-status could not tell a failed start
# from a provider that simply has not started yet.
_init_failures: dict[str, str] = {}


def _home_key(home: str | Path) -> str:
    return str(Path(home).expanduser().resolve())


def _live_provider_for(home: str | Path) -> BasicMemoryProvider | None:
    with _registry_lock:
        ref = _live_providers.get(_home_key(home))
    provider = ref() if ref is not None else None
    if provider is not None and provider._initialized and provider._actor is not None:
        return provider
    return None


def _is_connection_lost(exc: BaseException, actor: _BmMcpActor) -> bool:
    """True when a call failed because the `bm mcp` child is gone."""
    if not actor.is_alive():
        return True
    return type(exc).__name__ in {"ClosedResourceError", "BrokenResourceError"} or (
        "connection closed" in str(exc).lower()
    )


# Minimum gap between restarts of a dead `bm mcp`, so a server that dies on
# every start is not respawned on every call.
_RESTART_BACKOFF_SECONDS = 10.0


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
        self._agent_context: str = "primary"
        self._session_note_id: str | None = None
        self._continues_note_id: str | None = None
        self._session_started_at: datetime | None = None
        self._prefetch_thread: threading.Thread | None = None
        self._prefetch_lock = threading.Lock()
        self._pending_prefetch: str = ""
        self._pending_prefetch_count: int = 0
        self._last_recall_count: int = 0
        # Serializes session-scoped capture state (note id, opener) between turn
        # capture, session-end summaries and session switches.
        self._state_lock = threading.RLock()
        self._restart_lock = threading.Lock()
        self._last_restart_at: float = 0.0
        self._failure_count: int = 0
        self._failure_pause_until: float = 0.0
        self._initialized: bool = False
        self._first_user_msg: str | None = None
        _all_providers.add(self)

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
        requested_session_id = session_id or ""
        self._hermes_home = kwargs.get("hermes_home") or os.path.expanduser("~/.hermes")
        # Hermes passes "cron", "subagent" or "flush" for non-conversational
        # agents; their prompts must not land in the user's transcripts.
        self._agent_context = kwargs.get("agent_context") or "primary"
        self._apply_config(self._hermes_home)

        if not _MCP_AVAILABLE:
            logger.error(
                "basic-memory: MCP SDK unavailable; provider will not initialize: %s",
                _MCP_IMPORT_ERROR,
            )
            self._record_init_failure(
                f"the mcp Python package is not importable: {_MCP_IMPORT_ERROR}"
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
                self._record_init_failure("bm CLI not found, and uv is not installed to add it")
                return
            logger.info(
                "basic-memory: bm CLI not found — installing basic-memory via "
                "`uv tool install` (one-time bootstrap)"
            )
            if _install_bm_via_uv() is None:
                logger.error(
                    "basic-memory: auto-install via uv failed. Run "
                    "`uv tool install basic-memory --prerelease=allow` manually to debug. "
                    "Provider will not initialize."
                )
                self._record_init_failure(
                    "`uv tool install basic-memory --prerelease=allow` failed; "
                    "run it by hand to see why"
                )
                return

        # Trigger: initialize() called while a previous actor is still running
        # (Hermes re-initializes providers per session; _ensure_slash_ready's
        # lazy init can also precede a later session initialize).
        # Why: every _BmMcpActor owns one `bm mcp` child process. Allocating a
        # fresh actor without stopping the old one orphans that child — issue
        # #1017 saw 18 idle `bm mcp` processes (~2.3 GB) accumulate this way.
        # Outcome: a healthy actor is reused — its argv is always `bm mcp` and
        # project routing happens per call, so the config re-read above never
        # invalidates the connection. A dead actor is shut down first (joining
        # its thread reaps the child) and then replaced below.
        if self._actor is not None and self._actor.is_alive():
            if not self._ensure_project(self._actor):
                return
            self._mark_session_ready(requested_session_id)
            logger.info(
                "basic-memory provider ready (reusing running MCP actor): mode=%s project=%s",
                self._mode,
                self._project,
            )
            return
        if self._actor is not None:
            try:
                self._actor.shutdown(timeout=5.0)
            except Exception as e:
                logger.debug("stale actor shutdown during initialize: %s", e)
            self._actor = None

        try:
            argv = self._server_argv()
        except Exception as e:
            logger.error("basic-memory: cannot determine server argv: %s", e)
            self._record_init_failure(f"cannot build the `bm mcp` command: {e}")
            return

        # Expose the actor to cleanup BEFORE start() blocks (up to 25s):
        # _atexit_cleanup and the SIGTERM handler only reach actors through
        # provider.shutdown() → self._actor, so a local-only actor would leak
        # its freshly spawned `bm mcp` child if a signal lands mid-start.
        # Callers can't observe the half-started actor: every tool/capture
        # path gates on _initialized, which stays False until start() returns.
        actor = _BmMcpActor(argv)
        self._actor = actor
        try:
            actor.start(timeout=25.0)
        except Exception as e:
            try:
                actor.shutdown(timeout=5.0)
            except Exception as shutdown_error:
                logger.debug("actor shutdown after failed start: %s", shutdown_error)
            if self._actor is actor:
                self._actor = None
            logger.error("basic-memory: MCP server failed to start: %s", e)
            self._record_init_failure(f"`bm mcp` failed to start: {e}")
            return
        if self._actor is not actor:
            # Signal/atexit cleanup detached the actor while start() was
            # waiting. Do not resurrect a provider whose process cleanup ran.
            try:
                actor.shutdown(timeout=5.0)
            except Exception as e:
                logger.debug("actor shutdown after interrupted start: %s", e)
            return

        tools = actor.list_tools()
        names = {t["name"] for t in tools}
        missing = [bm for bm in _HERMES_TO_BM.values() if bm not in names]
        if missing:
            logger.warning(
                "basic-memory: BM MCP missing expected tools: %s (got: %s)",
                missing,
                sorted(names),
            )

        if not self._ensure_project(actor):
            return
        self._mark_session_ready(requested_session_id)
        logger.info(
            "basic-memory provider ready: mode=%s project=%s tools=%d",
            self._mode,
            self._project,
            len(tools),
        )

    def _mark_session_ready(self, session_id: str) -> None:
        """Publish a successful session boundary while retaining the MCP actor."""
        if session_id != self._session_id:
            # Trigger: Hermes starts a distinct session on a reused provider.
            # Why: these fields identify one transcript and its opening turn;
            # retaining them appends the new session to the previous note.
            # Outcome: only session-scoped capture state resets. Reconnecting a
            # dead actor for the same session preserves the in-progress note.
            self._session_id = session_id
            self._session_note_id = None
            self._first_user_msg = None
            self._session_started_at = datetime.now(timezone.utc)
        elif self._session_started_at is None:
            self._session_started_at = datetime.now(timezone.utc)
        self._initialized = True
        with _registry_lock:
            key = _home_key(self._hermes_home)
            _live_providers[key] = weakref.ref(self)
            _init_failures.pop(key, None)

    def _record_init_failure(self, reason: str) -> None:
        """Remember why initialize() gave up, for /bm-status to report."""
        with _registry_lock:
            _init_failures[_home_key(self._hermes_home)] = reason

    def _apply_config(self, hermes_home: str) -> None:
        """Load the profile's basic-memory.json settings. Reads one file; starts nothing."""
        cfg = _load_config(hermes_home)
        self._mode = cfg.get("mode") or "local"
        self._project = cfg.get("project") or _default_project()
        self._project_path = os.path.expanduser(cfg.get("project_path") or _default_project_path())
        self._capture_per_turn = bool(_coerce_bool(cfg.get("capture_per_turn", True)))
        self._capture_session_end = bool(_coerce_bool(cfg.get("capture_session_end", True)))
        self._capture_folder = cfg.get("capture_folder") or "hermes-sessions"
        self._remember_folder = cfg.get("remember_folder") or "bm-remember"

    def _ensure_project(self, actor: _BmMcpActor) -> bool:
        """Make sure Basic Memory knows the configured project, through `bm mcp`.

        Trigger: every initialize(), once a live `bm mcp` is in hand.
        Why: Basic Memory owns its project registry (docs/DOMAIN_MODEL.md,
          "Project Registry Authority"). Reading its config file from here
          meant copying its directory rules (BASIC_MEMORY_CONFIG_DIR,
          XDG_CONFIG_HOME), and a stale copy rejected registered projects
          (#2019). Probing with `bm project add` rebuilt the indexes of an
          existing project and outlived its timeout.
        Outcome: True when the project is listed (in local mode, registered in
          the local config), or was missing in local mode and is now created. Otherwise the failure is logged and recorded,
          the `bm mcp` child is stopped, and the provider stays uninitialized.
        """
        try:
            listing = json.loads(
                actor.call("bm_project", {"command": "list", "output_format": "json"}, timeout=60.0)
            )
            if "error" in listing:
                return self._project_check_failed(
                    f"could not list Basic Memory projects: {listing['error']}"
                )
            if _project_listed(listing["projects"], self._project, local=self._mode == "local"):
                return True

            # Trigger: the project is missing in cloud mode.
            # Why: a cloud project lives in a workspace the user chooses; creating
            #   one silently could put notes in the wrong place.
            # Outcome: refuse with the command that registers it.
            if self._mode != "local":
                return self._project_check_failed(
                    f"project {self._project!r} is not in Basic Memory's project list. "
                    f"Run `bm project add {self._project} --cloud` "
                    "(optionally with --local-path) first"
                )

            os.makedirs(self._project_path, exist_ok=True)
            created = json.loads(
                actor.call(
                    "bm_project",
                    {
                        "command": "create",
                        "name": self._project,
                        "path": self._project_path,
                        "output_format": "json",
                    },
                    timeout=60.0,
                )
            )
        except Exception as e:
            return self._project_check_failed(f"could not check project {self._project!r}: {e}")
        if "error" in created:
            return self._project_check_failed(
                f"could not create project {self._project!r} at {self._project_path}: "
                f"{created['error']}"
            )
        logger.info("basic-memory: created project %r at %s", self._project, self._project_path)
        return True

    def _project_check_failed(self, reason: str) -> bool:
        """Record why the project check failed and release the `bm mcp` child."""
        logger.error("basic-memory: %s. Provider will not initialize.", reason)
        self._record_init_failure(reason)
        # A reused actor can belong to a provider that served an earlier session.
        self._initialized = False
        actor, self._actor = self._actor, None
        if actor is not None:
            try:
                actor.shutdown(timeout=5.0)
            except Exception as e:
                logger.debug("actor shutdown after failed project check: %s", e)
        return False

    def _server_argv(self) -> list[str]:
        bm = _bm_binary_path()
        if not bm:
            raise RuntimeError("bm CLI not found on PATH")
        # `bm mcp` works the same in local and cloud modes — bm reads its own
        # config (honoring BASIC_MEMORY_CONFIG_DIR and XDG_CONFIG_HOME, which
        # the child inherits) to know whether a project is local or
        # cloud-routed. We pass --project on each tool call.
        return [bm, "mcp"]

    def shutdown(self) -> None:
        if self._hermes_home:
            key = _home_key(self._hermes_home)
            with _registry_lock:
                ref = _live_providers.get(key)
                if ref is not None and ref() is self:
                    del _live_providers[key]
        if self._actor is not None:
            try:
                self._actor.shutdown(timeout=5.0)
            except Exception as e:
                logger.debug("actor shutdown: %s", e)
        self._actor = None
        self._initialized = False

    # ---- Calling Basic Memory ----
    def _call(self, bm_tool: str, bm_args: dict[str, Any], timeout: float) -> str:
        """Call a bm MCP tool, restarting a dead `bm mcp` once and retrying.

        Trigger: the child exited mid-session ("Connection closed").
        Why: the actor never reconnects on its own, so every later call failed
        until the gateway restarted.
        Outcome: re-run initialize(), which replaces a dead actor and keeps the
        same session's capture state, then retry the call once.
        """
        actor = self._actor
        if actor is None:
            raise RuntimeError("basic-memory MCP actor not started")
        try:
            return actor.call(bm_tool, bm_args, timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise
        except Exception as e:
            if not _is_connection_lost(e, actor) or not self._restart(actor):
                raise
            logger.warning("basic-memory: bm mcp connection lost (%s); restarted it", e)
            assert self._actor is not None
            return self._actor.call(bm_tool, bm_args, timeout=timeout)

    def _restart(self, dead: _BmMcpActor) -> bool:
        with self._restart_lock:
            if self._actor is not dead:
                return self._actor is not None and self._actor.is_alive()
            now = time.monotonic()
            if now - self._last_restart_at < _RESTART_BACKOFF_SECONDS:
                return False
            self._last_restart_at = now
            dead.shutdown(timeout=2.0)
            self._initialized = False
            self.initialize(
                self._session_id,
                hermes_home=self._hermes_home,
                agent_context=self._agent_context,
            )
            return self._initialized and self._actor is not None and self._actor is not dead

    # ---- Tool surface ----
    def system_prompt_block(self) -> str:
        if not self._initialized:
            return ""
        return (
            "## Basic Memory Knowledge Graph\n"
            f"Active project: `{self._project}` ({self._mode}).\n"
            "\n"
            "**Use the `bm_*` tools directly — do not shell out to the `bm` CLI.** "
            "These tools route through a persistent MCP connection "
            "(~0.1s/call); running `bm` from the shell spawns a fresh Python "
            "process per call (~1-2s) and bypasses Hermes's automatic per-turn "
            "capture.\n"
            "\n"
            "Use `bm_search` before answering about prior decisions, projects, "
            "meetings, or anything that might already be documented.\n"
            "\n"
            "**Saves must be real.** When the user asks you to remember, "
            "record, save, or note something, call `bm_write` (or `bm_edit`) "
            "in that same turn. Never say information was saved, recorded, or "
            "remembered unless the tool call returned a result, and when "
            "confirming a save quote the `permalink` from that result — never "
            "a filename recalled from memory.\n"
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
            result = self._call(bm_tool, bm_args, timeout=30.0)
        except Exception as e:
            self._record_failure(e)
            logger.exception("bm tool call failed: %s", tool_name)
            return tool_error(f"{tool_name}: {e}")
        self._record_success()
        return result

    # ---- Recall (retrieve step) ----
    def _recall(self, query: str, timeout: float) -> tuple[str, int]:
        """Keyword recall for a turn: (formatted block, number of notes)."""
        # mode="text" — bypass BM's "hybrid" default which mixes FTS
        # with vector search. Vector indexing is scheduled asynchronously
        # in BM (see services/search_service.py:_schedule_vector_sync_if_enabled),
        # so hybrid search can miss notes that were just written, especially
        # under cold-start or load. Prefetch is a recall hot path with a
        # 3s budget and the queries are usually keyword-like — FTS-only is
        # both faster and more deterministic.
        raw = self._call(
            "bm_search",
            {
                "project": self._project,
                "query": _truncate(query, 500),
                "page_size": 5,
                "mode": "text",
                "output_format": "json",
            },
            timeout=timeout,
        )
        return self._format_prefetch_counted(raw, query)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        # recall_status() must describe only this prefetch, never a prior one.
        self._last_recall_count = 0
        with self._prefetch_lock:
            cached, cached_count = self._pending_prefetch, self._pending_prefetch_count
            self._pending_prefetch, self._pending_prefetch_count = "", 0
        if cached:
            self._last_recall_count = cached_count
            return cached
        if not self._initialized or self._actor is None or self._is_circuit_open():
            return ""
        try:
            text, self._last_recall_count = self._recall(query, timeout=3.0)
        except Exception as e:
            self._record_failure(e)
            return ""
        self._record_success()
        return text

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._initialized or self._actor is None or self._is_circuit_open():
            return
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            return

        def _bg() -> None:
            try:
                # The background path can afford a longer timeout than prefetch().
                text, count = self._recall(query, timeout=10.0)
            except Exception as e:
                self._record_failure(e)
                logger.debug("queue_prefetch bg failed: %s", e)
                return
            with self._prefetch_lock:
                self._pending_prefetch, self._pending_prefetch_count = text, count

        self._prefetch_thread = _spawn_thread(_bg, name="bm-prefetch")

    def recall_status(self) -> RecallStatus | None:
        """What the most recent prefetch injected, for Hermes's recall indicator."""
        if not self._last_recall_count:
            return None
        return RecallStatus(provider_label="Basic Memory", count=self._last_recall_count)

    def _format_prefetch(self, raw_json: str) -> str:
        return self._format_prefetch_counted(raw_json, "")[0]

    def _format_prefetch_counted(self, raw_json: str, query: str) -> tuple[str, int]:
        try:
            data = json.loads(raw_json)
        except Exception:
            return "", 0
        # BM may return JSON directly or wrapped {"text": "..."}
        if isinstance(data, dict) and "text" in data and "results" not in data:
            try:
                data = json.loads(data["text"])
            except Exception:
                return "", 0
        results = data.get("results") if isinstance(data, dict) else None
        if not results:
            return "", 0
        lines = ["## Basic Memory Recall"]
        for r in list(results)[:5]:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "(untitled)")
            permalink = str(r.get("permalink") or "")
            preview_raw = r.get("content") or r.get("preview") or ""
            if not isinstance(preview_raw, str):
                preview_raw = str(preview_raw)
            preview = _snippet_around(preview_raw, query, width=240)
            lines.append(f"- **{title}** (`{permalink}`) — {preview}")
        count = len(lines) - 1
        return ("\n".join(lines), count) if count else ("", 0)

    # ---- Per-turn capture (extract step) ----
    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
        turn_author: dict[str, Any] | None = None,
    ) -> None:
        # Constraint: Hermes v0.22 runs sync_turn on the memory manager's single
        # background worker, which orders it against session switches and other
        # writes. A thread of our own would let a turn land after a /new in the
        # previous session's transcript, so the capture runs inline.
        if (
            not self._capture_per_turn
            or not self._initialized
            or self._actor is None
            or self._agent_context != "primary"
            or self._is_circuit_open()
        ):
            return
        author = turn_author.get("name") if turn_author else None
        try:
            with self._state_lock:
                if self._first_user_msg is None and user_content:
                    self._first_user_msg = _truncate(_public_text(user_content), 240)
                self._capture_turn(user_content, assistant_content, author=author)
        except Exception as e:
            self._record_failure(e)
            logger.warning("basic-memory sync_turn failed: %s", e)
            return
        self._record_success()

    def _capture_turn(self, user: str, asst: str, *, author: str | None = None) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        # A shared channel has several participants; name who wrote this turn.
        who = f"User ({author})" if author else "User"
        block = (
            f"### {ts}\n\n"
            f"**{who}:** {_truncate(_public_text(user), 4000)}\n\n"
            f"**Assistant:** {_truncate(_public_text(asst), 4000)}\n"
        )
        if not self._session_note_id:
            title = self._session_note_title()
            relations = ""
            if self._continues_note_id:
                relations = f"## Relations\n\n- continues [[{self._continues_note_id}]]\n\n"
            content = (
                f"# {title}\n\n"
                f"Live transcript of Hermes session "
                f"`{self._session_id or '(no-id)'}`. "
                f"Auto-captured by basic-memory provider.\n\n"
                f"{relations}"
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
            raw = self._call("bm_write", args, timeout=15.0)
            if "ALREADY_EXISTS" not in raw:
                self._session_note_id = _extract_permalink(raw, fallback=title)
                return
            # Trigger: a note with this title exists (a restart within the same
            # minute and session). Why: bm 1.0 refuses to overwrite and the turn
            # would be lost. Outcome: append to the existing transcript instead.
            self._session_note_id = f"{self._capture_folder}/{title}.md"
        args = {
            "project": self._project,
            "identifier": self._session_note_id,
            "command": "append",
            "content": "\n" + block,
        }
        self._call("bm_edit", args, timeout=15.0)

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

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        """Rebind capture state when Hermes rotates the session id without teardown.

        Fires on /new, /reset, /resume, /branch and context compression. Any new
        id starts a new transcript note, so later turns never land in the previous
        session's record. When the conversation continues under the new id
        (compression, /branch) the new note links back with a `continues`
        relation. A rewind (/undo) keeps the current note.
        """
        if rewound or not new_session_id:
            return
        with self._state_lock:
            if new_session_id == self._session_id and not reset:
                return
            previous_note = self._session_note_id
            continues = (
                not reset
                and previous_note is not None
                and parent_session_id in ("", self._session_id)
            )
            self._session_id = new_session_id
            self._session_note_id = None
            self._continues_note_id = previous_note if continues else None
            self._first_user_msg = None
            self._session_started_at = datetime.now(timezone.utc)
        with self._prefetch_lock:
            self._pending_prefetch, self._pending_prefetch_count = "", 0

    # ---- Session-end summary ----
    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        if (
            not self._capture_session_end
            or not self._initialized
            or self._actor is None
            or self._agent_context != "primary"
        ):
            return
        try:
            with self._state_lock:
                self._write_summary(messages)
        except Exception as e:
            logger.warning("basic-memory on_session_end summary failed: %s", e)

    def _write_summary(self, messages: list[dict[str, Any]]) -> None:
        user_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        asst_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"]
        if not user_msgs and not self._session_note_id:
            # Gateway expiry ends idle sessions too; a summary of nothing is noise.
            return
        first_user = self._first_user_msg or ""
        if not first_user and user_msgs:
            first_user = _truncate(
                _public_text(_join_message_content(user_msgs[0].get("content"))), 240
            )
        last_asst = ""
        if asst_msgs:
            last_asst = _truncate(
                _public_text(_join_message_content(asst_msgs[-1].get("content"))), 600
            )
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
            # A session can end more than once (gateway expiry after /new); the
            # newest summary replaces the earlier one.
            "overwrite": True,
        }
        try:
            self._call("bm_write", args, timeout=15.0)
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

    def _record_success(self) -> None:
        """A call worked, so earlier failures were not consecutive: forget them."""
        if self._failure_pause_until == 0.0:
            self._failure_count = 0

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
    "bm-status": (
        "Usage: /bm-status\n"
        "Show whether Basic Memory is ready. Status never starts bm: the plugin starts "
        "`bm mcp` on the first /bm-* command or agent session, so 'idle' is healthy."
    ),
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

# Providers started on demand for /bm-* commands, one per profile home, used only
# when that profile has no initialized agent provider to borrow.
_slash_providers: dict[str, BasicMemoryProvider] = {}


def _current_hermes_home() -> str:
    """The caller's Hermes profile home.

    Hermes v0.22 scopes HERMES_HOME per profile with a context variable, which
    `hermes_constants.get_hermes_home()` honors; v0.20 reads the environment.
    """
    try:
        from hermes_constants import get_hermes_home  # type: ignore
    except ImportError:
        return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return str(get_hermes_home())


def _resolve_slash_provider(cmd: str) -> tuple[BasicMemoryProvider | None, str | None]:
    """The provider a /bm-* command uses: the profile's live agent provider if
    one exists (sharing its `bm mcp`), else one started once for that profile."""
    home = _current_hermes_home()
    live = _live_provider_for(home)
    if live is not None:
        return live, None
    with _slash_init_lock:
        provider = _slash_providers.setdefault(_home_key(home), BasicMemoryProvider())
        provider._hermes_home = provider._hermes_home or home
    err = _ensure_slash_ready(provider, cmd)
    return (None, err) if err else (provider, None)


def _peek_slash_provider() -> BasicMemoryProvider:
    """A provider describing the caller's profile, without starting bm.

    The profile's live provider when one exists. Otherwise a fresh instance
    carrying the profile's basic-memory.json, so /bm-status reports the
    configured project rather than the built-in defaults.
    """
    home = _current_hermes_home()
    live = _live_provider_for(home)
    if live is not None:
        return live
    view = BasicMemoryProvider()
    view._hermes_home = home
    view._apply_config(home)
    return view


class _Readiness(NamedTuple):
    """What /bm-status says about a provider, worked out without starting bm.

    A NamedTuple, not a dataclass: @dataclass looks the module up in
    sys.modules, and loaders that skip registering it (the hermetic test
    conftest does) would fail to import the plugin.
    """

    status: str  # "ready" or "unavailable"
    process: str  # the `bm mcp` child: running, idle, stopped or not running
    reason: str | None = None  # why it is unavailable
    notes: tuple[str, ...] = ()  # setup that the first start will do


def _start_blocker(provider: BasicMemoryProvider) -> str | None:
    """The reason initialize() is certain to fail, from local checks only.

    Mirrors initialize()'s preconditions without its side effects: no install,
    no subprocess. Project registration is not checked here: only a running
    `bm mcp` can answer that, and a failed check is reported as the last
    start's failure instead.
    """
    if not _MCP_AVAILABLE:
        return "the mcp Python package is not installed"
    if not _bm_binary_path() and not _uv_binary_path():
        return "bm CLI not found, and uv is not installed to add it"
    return None


def _readiness(provider: BasicMemoryProvider, home: str) -> _Readiness:
    # --- Started: the provider owns a `bm mcp` child ---
    if provider._initialized and provider._actor is not None:
        if provider._actor.is_alive():
            return _Readiness("ready", "running")
        return _Readiness("ready", "stopped (restarts on the next call)")

    # --- Not started: is anything certain to stop the first start? ---
    blocker = _start_blocker(provider)
    if blocker is not None:
        return _Readiness("unavailable", "not running", reason=blocker)

    # Trigger: an earlier initialize() for this profile gave up.
    # Why: the cause (a crashed `bm mcp`, a failed install) is not visible
    #   from local checks, and "idle" would hide it.
    # Outcome: report the recorded failure; the next use tries again.
    with _registry_lock:
        failure = _init_failures.get(_home_key(home))
    if failure is not None:
        return _Readiness(
            "unavailable",
            "not running (retried on next use)",
            reason=f"last start failed: {failure}",
        )

    # --- Healthy and idle: the plugin starts `bm mcp` lazily ---
    notes: list[str] = []
    if not _bm_binary_path():
        notes.append("bm CLI is installed with uv on first use")
    return _Readiness(
        "ready",
        "idle (starts on the next /bm-* command or agent session)",
        notes=tuple(notes),
    )


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
                hermes_home=provider._hermes_home or _current_hermes_home(),
                agent_context="slash",
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
    provider: BasicMemoryProvider | None = None,
) -> list[tuple[str, Callable[[str], str], str, str]]:
    """
    Return (name, handler, description, args_hint) tuples for each /bm-* command.

    With `provider`, handlers use that instance. Without it (production), each
    call resolves the caller's profile provider, which Hermes v0.22's
    per-agent, per-profile instances require.
    """

    def _get(cmd: str) -> tuple[BasicMemoryProvider | None, str | None]:
        if provider is not None:
            err = _ensure_slash_ready(provider, cmd)
            return (None, err) if err else (provider, None)
        return _resolve_slash_provider(cmd)

    def _bm_search(raw_args: str) -> str:
        args = raw_args.strip()
        if not args or _is_help_arg(args):
            return _SLASH_USAGE["bm-search"]
        p, err = _get("bm-search")
        if err:
            return err
        try:
            raw = p._call(
                "bm_search",
                {
                    "project": p._project,
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
        p, err = _get("bm-read")
        if err:
            return err
        try:
            raw = p._call(
                "bm_read",
                {
                    "project": p._project,
                    "identifier": args,
                    "include_frontmatter": False,
                    "output_format": "text",
                },
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
        p, err = _get("bm-context")
        if err:
            return err
        try:
            raw = p._call(
                "bm_context",
                {
                    "project": p._project,
                    "identifier": args,
                    "depth": 1,
                    "output_format": "text",
                },
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
        p, err = _get("bm-recent")
        if err:
            return err
        try:
            raw = p._call(
                "bm_project",
                {
                    "project": p._project,
                    "command": "recent",
                    "since": timeframe,
                    "page_size": 10,
                    "output_format": "json",
                },
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-recent: {e}"
        data = _unwrap_json_or_text(raw)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return "bm-recent: invalid activity response"
        results = data["results"]
        return _format_result_rows(
            results,
            header=f"Basic Memory activity ({timeframe}):",
            empty_msg=f"No activity in the last {timeframe}.",
        )

    def _bm_status(raw_args: str) -> str:
        if _is_help_arg(raw_args):
            return _SLASH_USAGE["bm-status"]
        p = provider if provider is not None else _peek_slash_provider()
        readiness = _readiness(p, p._hermes_home or _current_hermes_home())
        lines = ["Basic Memory plugin status", f"  Status:      {readiness.status}"]
        if readiness.reason:
            lines.append(f"  Reason:      {readiness.reason}")
        lines.append(f"  MCP process: {readiness.process}")
        lines.extend(f"  Note:        {note}" for note in readiness.notes)
        lines += [
            f"  Provider:    {p.name}",
            f"  Mode:        {p._mode}",
            f"  Project:     {p._project}",
        ]
        if p._mode == "local":
            lines.append(f"  Path:        {p._project_path}")
        bm_bin = _bm_binary_path()
        lines.append(f"  bm CLI:      {bm_bin or '(not found)'}")
        lines.append(f"  MCP module:  {'available' if _MCP_AVAILABLE else 'missing'}")
        lines.append(
            f"  Capture:     per-turn={p._capture_per_turn}, "
            f"session-end={p._capture_session_end}, "
            f"folder={p._capture_folder!r}"
        )
        lines.append(f"  Remember folder: {p._remember_folder!r}")
        if p._failure_count:
            circuit = "open" if p._is_circuit_open() else "closed"
            lines.append(f"  Failures:    {p._failure_count} (circuit {circuit})")
        return "\n".join(lines)

    def _bm_remember(raw_args: str) -> str:
        text = raw_args.strip()
        if not text or _is_help_arg(text):
            return _SLASH_USAGE["bm-remember"]
        p, err = _get("bm-remember")
        if err:
            return err
        title = _remember_title(text)
        folder = p._remember_folder or "bm-remember"
        try:
            raw = p._call(
                "bm_write",
                {
                    "project": p._project,
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
        p, err = _get("bm-project")
        if err:
            return err
        try:
            raw = p._call(
                "bm_project",
                {"command": "list", "output_format": "json"},
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
        for proj in projects:
            if isinstance(proj, dict):
                name = str(proj.get("name") or proj.get("permalink") or "(unnamed)")
                src = proj.get("source") or proj.get("workspace") or ""
            else:
                name, src = str(proj), ""
            marker = "  (active)" if name == p._project else ""
            tag = f" [{src}]" if src else ""
            lines.append(f"- {name}{tag}{marker}")
        return "\n".join(lines)

    def _bm_workspace(raw_args: str) -> str:
        if _is_help_arg(raw_args):
            return _SLASH_USAGE["bm-workspace"]
        p, err = _get("bm-workspace")
        if err:
            return err
        if p._mode != "cloud":
            return (
                "Workspaces are a Basic Memory Cloud concept. "
                f"This plugin is in '{p._mode}' mode — no workspaces to list."
            )
        try:
            raw = p._call(
                "bm_project",
                {"command": "list", "output_format": "json"},
                timeout=15.0,
            )
        except Exception as e:
            return f"bm-workspace: {e}"
        data = _unwrap_json_or_text(raw)
        workspaces: dict[str, dict[str, Any]] = {}
        if isinstance(data, dict):
            for project in data.get("projects", []):
                tenant_id = project.get("workspace_tenant_id")
                if tenant_id:
                    workspaces[tenant_id] = {
                        "name": project.get("workspace_name"),
                        "workspace_type": project.get("workspace_type"),
                        "is_default": project.get("workspace_is_default"),
                    }
        if not workspaces:
            return "No Basic Memory Cloud workspaces found."
        lines = ["Basic Memory Cloud workspaces:"]
        for w in workspaces.values():
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
# to delegate to PluginManager. Until that is available, we write only the
# capabilities the supplied context does not support. A modern PluginContext
# owns the registered objects' lifecycle, so duplicating its registrations in
# the private registries would replace those lifecycle-owned entries.
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
    provider: BasicMemoryProvider | None,
    skill_path: Path | None = None,
    *,
    register_commands: bool = True,
    register_skill: bool = True,
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

    plugin_commands = getattr(mgr, "_plugin_commands", None) if register_commands else None
    if register_commands:
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

    plugin_skills = getattr(mgr, "_plugin_skills", None) if register_skill else None
    if plugin_skills is not None and skill_path is not None and skill_path.exists():
        plugin_skills[f"{_PLUGIN_MANIFEST_NAME}:basic-memory"] = {
            "path": skill_path,
            "plugin": _PLUGIN_MANIFEST_NAME,
            "bare_name": "basic-memory",
            "description": _SKILL_DESCRIPTION,
        }


# ---------------------------------------------------------------------------
# atexit + SIGTERM safety nets (mirrors plugins/memory/openviking pattern)
# ---------------------------------------------------------------------------

# Weak: Hermes v0.22 constructs a provider per agent build, so a strong list
# grew for the life of the gateway. Live instances still get cleaned up.
_all_providers: weakref.WeakSet[BasicMemoryProvider] = weakref.WeakSet()


def _atexit_cleanup() -> None:
    for p in list(_all_providers):
        try:
            p.shutdown()
        except Exception:
            pass


atexit.register(_atexit_cleanup)


_sigterm_installed = False


def _install_sigterm_cleanup() -> None:
    """
    Run _atexit_cleanup on SIGTERM as well as at interpreter exit.

    atexit alone doesn't cover SIGTERM: Python's default action terminates the
    process without unwinding, so gateway restarts (systemd stop, docker stop,
    supervisor reload) orphan every `bm mcp` child (issue #1017).

    Constraint: this plugin is a library embedded in a host process (Hermes),
    so it must not stomp the host's signal handling:
      - existing Python handler → chain: run our cleanup, then the host's
        handler, preserving its semantics.
      - SIG_DFL → install: run cleanup, restore SIG_DFL, and re-deliver the
        signal so the process still dies by SIGTERM exactly as before.
      - SIG_IGN, or None (a C-level handler unreachable from Python, so
        chaining is impossible) → leave untouched.
    signal.signal only works from the main thread and SIGTERM only exists on
    platforms that define it, so this no-ops cleanly everywhere else.
    """
    global _sigterm_installed
    if _sigterm_installed:
        return
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is None:  # pragma: no cover - every supported platform defines SIGTERM
        return
    if threading.current_thread() is not threading.main_thread():
        return
    previous = signal.getsignal(sigterm)
    if previous is signal.SIG_IGN or previous is None:
        return

    def _handler(signum: int, frame: Any) -> None:
        _atexit_cleanup()
        if callable(previous):
            previous(signum, frame)
            return
        # previous is SIG_DFL: restore it and re-deliver so the process
        # terminates by SIGTERM exactly as it would have without us.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    try:
        signal.signal(sigterm, _handler)
    except (ValueError, OSError):  # pragma: no cover - main-thread race at shutdown
        return
    _sigterm_installed = True


_install_sigterm_cleanup()


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx: Any) -> None:
    """Register basic-memory as a memory provider plugin.

    Hermes v0.22 calls this once per load and refuses an instance another
    profile's manager already bound, so every call creates a fresh provider;
    the instance binds its profile in initialize(), never here.
    """
    provider = BasicMemoryProvider()
    ctx.register_memory_provider(provider)

    # Bundle the user-facing skill so `hermes plugins install` wires it up
    # with the rest of the plugin. The skill is opt-in via
    # `skill:view basic-memory:basic-memory` — it's not in the auto-loaded
    # `<available_skills>` index. Always-on agent guidance still flows
    # through `system_prompt_block()`.
    skill_path = Path(__file__).resolve().parent / "skill" / "SKILL.md"
    supports_skill_registration = hasattr(ctx, "register_skill")
    if skill_path.exists() and supports_skill_registration:
        # Forward-compat: if Hermes's memory-provider collector ever delegates
        # register_skill to PluginManager (or another loader passes us a real
        # PluginContext), this lands the skill via the supported path. The
        # legacy collectors without this API use the reach-in below.
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
    supports_command_registration = hasattr(ctx, "register_command")
    if supports_command_registration:
        # Handlers resolve the caller's profile provider per call, so they are
        # not tied to this (possibly never-initialized) instance.
        for name, handler, description, args_hint in _build_slash_commands():
            try:
                ctx.register_command(
                    name,
                    handler,
                    description=description,
                    args_hint=args_hint,
                )
            except Exception as e:
                logger.warning("basic-memory: register_command(%s) failed: %s", name, e)

    # Legacy Hermes collectors expose only register_memory_provider. Reach
    # into PluginManager solely for capabilities missing from that context;
    # modern contexts retain ownership metadata and can clean up registrations.
    if not supports_command_registration or not supports_skill_registration:
        _register_via_plugin_manager(
            None,
            skill_path=skill_path if skill_path.exists() else None,
            register_commands=not supports_command_registration,
            register_skill=not supports_skill_registration,
        )
