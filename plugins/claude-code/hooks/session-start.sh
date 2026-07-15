#!/usr/bin/env bash
#
# SessionStart hook — brief Claude from Basic Memory at the start of a session.
#
# This is the read side of the memory bridge: it puts the most relevant slice of
# the durable knowledge graph in front of Claude before the first prompt, so the
# session starts oriented instead of cold.
#
# Reads (all structured, all best-effort):
#   - the primary project's active tasks + open decisions
#   - open decisions from each configured shared/team project (secondaryProjects +
#     teamProjects), queried in parallel — this is the Phase 4 "recall reads across
#     the team" capability. Reads only; capture never touches a shared project.
#
# Contract: advisory, must NEVER disrupt a session. Every failure path exits 0 with
# no output. SessionStart adds plain stdout to Claude's context (verified — Q4),
# capped at 10,000 chars, so the brief stays small and bounded.

set -u

# --- Read the hook payload (stdin is JSON: cwd, source, session_id, ...) ---
# stdin can only be consumed once; capture it before anything else touches it.
input="$(cat 2>/dev/null || true)"

# --- Resolve how to invoke the Basic Memory CLI ---
# Prefer an explicit command when the host configured one, then a binary on PATH
# (fast — no per-call env resolution). Fall back to uvx / uv
# so the hook still works when Basic Memory was connected only as an ephemeral
# `uvx basic-memory mcp` server (the MCP setup our README recommends) with no
# persistent CLI installed — the uv cache is already warm from running the server.
# Trigger: none of basic-memory / bm / uvx / uv on PATH → BM isn't usable here.
# Outcome: silent no-op (the plugin must be invisible to non-BM users).
if [[ -n "${BM_BIN:-}" ]]; then
    BM="$BM_BIN"
elif command -v basic-memory >/dev/null 2>&1; then
    BM="basic-memory"
elif command -v bm >/dev/null 2>&1; then
    BM="bm"
elif command -v uvx >/dev/null 2>&1; then
    BM="uvx basic-memory"
elif command -v uv >/dev/null 2>&1; then
    BM="uv tool run basic-memory"
else
    exit 0
fi

# Everything else runs in one Python pass: parse config, run the queries, format
# the brief. Python is a guaranteed dependency (basic-memory requires it) and
# avoids brittle shell JSON wrangling. The payload and binary path cross over via
# the environment to sidestep argument-quoting issues.
# Resolve the hook script's own directory so the inline Python can find the
# shared envelope module. __file__ is '<stdin>' inside a heredoc, so the Python
# code can't locate itself — we pass the real path.
hook_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

BM_HOOK_INPUT="$input" BM_BIN="$BM" BM_HOOK_DIR="$hook_dir" python3 <<'PY' 2>/dev/null || exit 0
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

# --- Load the shared envelope module (lives two directories up in plugins/shared/) ---
# SessionStart is read-only (no note writes), so the envelope is only used for
# local event logging when captureEvents is enabled.
# Constraint: __file__ is '<stdin>' inside a bash heredoc, so the hook script's
#             real directory is passed in via the BM_HOOK_DIR environment variable.
_hook_dir = os.environ.get("BM_HOOK_DIR", "")
if _hook_dir:
    _shared_dir = os.path.join(_hook_dir, "..", "..", "shared")
    sys.path.insert(0, os.path.normpath(_shared_dir))
try:
    from harness_envelope import (
        SESSION_STARTED,
        append_to_event_log,
        create_envelope,
    )
    _HAS_ENVELOPE = True
except ImportError:
    _HAS_ENVELOPE = False

def command_argv(configured):
    """Preserve one literal executable path, otherwise parse a shell-style command."""
    # Trigger: Windows paths commonly contain spaces and backslashes.
    # Why: POSIX shlex would split the spaces and consume backslashes from an
    # unquoted native path such as C:\Program Files\Basic Memory\basic-memory.exe.
    # Outcome: an existing executable path stays one argv element; multi-token
    # launchers such as "uvx basic-memory" retain the existing command contract.
    if os.path.isfile(configured):
        return [configured]
    return shlex.split(configured)


bm_cmd = command_argv(os.environ.get("BM_BIN") or "basic-memory")

# Cloud project refs come in two unambiguous forms (names collide across
# workspaces, so a bare name won't route): a workspace-qualified name like
# "my-team-2/notes", or an external_id UUID. Detect the UUID to pick the flag.
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
# Cap how many shared projects we read per session — bounds latency and output.
MAX_SHARED = 6

# --- Resolve the working directory from the payload ---
try:
    payload = json.loads(os.environ.get("BM_HOOK_INPUT") or "{}")
except Exception:
    payload = {}
cwd = payload.get("cwd") or os.getcwd()


# --- Load plugin config from .claude settings (local overrides committed) ---
# Precedence (lowest to highest): the user-level ~/.claude/settings.json, then
# the project's .claude/settings.json and .claude/settings.local.json. A single
# user-level basicMemory block can cover every project without running setup per
# repo; any project can still pin its own mapping, which wins. We mirror Claude
# Code's real sources: user level is settings.json only (there is no user-level
# settings.local.json), local settings are project-scoped. Because the hook cwd
# can be a repo subdirectory, we walk ancestors to the nearest .claude config so
# a project-root mapping is honoured instead of skipped. `found` is True if any
# file declared a basicMemory block — its presence is the first-run sentinel
# (setup writing it stops the nudge below).
def _read_block(path):
    try:
        with open(path) as fh:
            block = json.load(fh).get("basicMemory")
    except Exception:
        return None
    return block if isinstance(block, dict) else None


def _project_dir(directory):
    # Nearest ancestor (including directory) holding a .claude settings file.
    d = os.path.abspath(directory)
    while True:
        for name in ("settings.json", "settings.local.json"):
            if os.path.isfile(os.path.join(d, ".claude", name)):
                return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.abspath(directory)
        d = parent


def load_settings(directory):
    merged = {}
    found = False
    home = os.path.expanduser("~")
    sources = [(home, ("settings.json",))]
    project = _project_dir(directory)  # already absolute
    if project != home:
        sources.append((project, ("settings.json", "settings.local.json")))
    for d, names in sources:
        for name in names:
            block = _read_block(os.path.join(d, ".claude", name))
            if block is not None:
                found = True
                merged.update(block)
    return merged, found


cfg, configured = load_settings(cwd)
primary_project = (cfg.get("primaryProject") or "").strip()
recall_timeframe = cfg.get("recallTimeframe") or "3d"
default_prompt = (
    "You have Basic Memory available for this project. Before answering recall "
    'questions ("what did we decide", "where did we leave off"), search the graph '
    "first — prefer structured filters (search_notes with type/status). When the "
    "user makes a material decision, capture it as a note with type: decision. "
    "Cite permalinks when referencing prior work."
)
recall_prompt = cfg.get("recallPrompt") or default_prompt
# Placement guidance — surfaced in the brief below so the output style's "follow the
# project's stored placement conventions" reflex has something concrete to follow.
# Without this, setup writes them but they never reach Claude (dead config).
placement_conventions = (cfg.get("placementConventions") or "").strip()
capture_folder = (cfg.get("captureFolder") or "sessions").strip()
capture_events = bool(cfg.get("captureEvents", False))
session_id = payload.get("session_id") or ""

# --- Resolve the shared/team read set ---
# secondaryProjects (read-only recall sources) + teamProjects keys (share targets,
# also read for recall). Dedup, preserve order, cap. These are read only — the
# capture hooks never write to them.
shared_refs = []
# Guard the JSON types: a misconfigured string would otherwise be iterated
# character-by-character, firing a bogus per-character query for each one.
secondary = cfg.get("secondaryProjects")
secondary = secondary if isinstance(secondary, list) else []
team = cfg.get("teamProjects")
team = team if isinstance(team, dict) else {}
for ref in list(secondary) + list(team.keys()):
    if isinstance(ref, str) and ref.strip() and ref.strip() != primary_project:
        if ref.strip() not in shared_refs:
            shared_refs.append(ref.strip())
shared_capped = len(shared_refs) > MAX_SHARED
shared_refs = shared_refs[:MAX_SHARED]


# --- Structured query helper (best-effort, per-call timeout) ---
# project_ref=None routes to the user's default project (zero-config usefulness).
# A UUID ref routes via --project-id; a qualified name via --project.
def search(filters, project_ref=None, timeout=10):
    cmd = [*bm_cmd, "tool", "search-notes", *filters, "--page-size", "5"]
    if project_ref:
        flag = "--project-id" if UUID_RE.match(project_ref) else "--project"
        cmd += [flag, project_ref]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


ACTIVE_TASKS = ["--type", "task", "--status", "active"]
OPEN_DECISIONS = ["--type", "decision", "--status", "open"]
# Recent session checkpoints carry the resume cursor. This is the one query the
# `recallTimeframe` window applies to — tasks and decisions are status-scoped (an
# old open decision is still open), but "recent sessions" is inherently time-scoped.
RECENT_SESSIONS = ["--type", "session", "--after_date", recall_timeframe]

# --- Run everything concurrently ---
# Cloud reads cost a network round-trip each; parallelism keeps total wall-clock at
# ~one query instead of the sum. Each call is independently best-effort.
# Size the pool to cover every submitted search (3 primary + up to MAX_SHARED),
# so none queues — a queued call could otherwise serialize behind a slow one and
# push the hook past Claude Code's SessionStart timeout before the brief prints.
with ThreadPoolExecutor(max_workers=3 + MAX_SHARED) as pool:
    fut_tasks = pool.submit(search, ACTIVE_TASKS, primary_project or None)
    fut_decisions = pool.submit(search, OPEN_DECISIONS, primary_project or None)
    fut_sessions = pool.submit(search, RECENT_SESSIONS, primary_project or None)
    fut_shared = {ref: pool.submit(search, OPEN_DECISIONS, ref) for ref in shared_refs}
    primary_tasks = fut_tasks.result()
    primary_decisions = fut_decisions.result()
    primary_sessions = fut_sessions.result()
    shared_results = {ref: fut.result() for ref, fut in fut_shared.items()}

# The first-run nudge — shown until setup writes a basicMemory config block.
setup_nudge = (
    "_Basic Memory isn't set up for this project yet. Run "
    "`/basic-memory:bm-setup` (~2 min) to configure session briefings and checkpoints._"
)

# Trigger: every primary query failed (no default project, misnamed project,
# unreachable cloud, transient error). Why: a broken query must never error the
# session, but it must not silently look like "nothing tracked" either.
# Outcome: first-run → setup nudge; configured-but-broken → a one-line signal so
# the user can tell a typo'd/unreachable project from an empty one.
if primary_tasks is None and primary_decisions is None and primary_sessions is None:
    if not configured:
        print("# Basic Memory\n\n" + setup_nudge)
    else:
        proj = primary_project or "the default project"
        print(
            "# Basic Memory\n\n"
            f"_Couldn't read from `{proj}` — it may be misnamed or unreachable. "
            "Run `/basic-memory:bm-status` to check._"
        )
    sys.exit(0)


def label(result):
    name = result.get("title") or result.get("file_path") or "(untitled)"
    ref = result.get("permalink") or result.get("file_path") or ""
    return f"- {name}" + (f" — {ref}" if ref else "")


def readable(ref):
    # Qualified names ("my-team-2/notes") read fine as-is; UUIDs get shortened.
    return f"shared project {ref[:8]}…" if UUID_RE.match(ref) else ref


def rows(result):
    return (result or {}).get("results") or []


# --- Assemble the brief (plain stdout → Claude's context) ---
lines = ["# Basic Memory — session context", ""]
header = f"**Project:** {primary_project or 'default project'}"
if shared_refs:
    header += f" · reading {len(shared_refs)} shared project(s)"
lines.append(header)

task_rows = rows(primary_tasks)
decision_rows = rows(primary_decisions)
session_rows = rows(primary_sessions)
if task_rows:
    lines += ["", f"## Active tasks ({len(task_rows)})", *[label(r) for r in task_rows]]
if decision_rows:
    lines += ["", f"## Open decisions ({len(decision_rows)})", *[label(r) for r in decision_rows]]
if session_rows:
    lines += [
        "",
        f"## Recent sessions ({len(session_rows)}) — where you left off",
        *[label(r) for r in session_rows],
    ]
if not (task_rows or decision_rows or session_rows):
    lines += ["", "_No active tasks, open decisions, or recent sessions in this project._"]

# --- Shared/team context (read-only) ---
shared_sections = [(ref, rows(shared_results.get(ref))) for ref in shared_refs]
shared_sections = [(ref, items) for ref, items in shared_sections if items]
if shared_sections:
    lines += ["", "## From shared projects (read-only)"]
    for ref, items in shared_sections:
        lines += [f"### {readable(ref)} — open decisions", *[label(r) for r in items]]
    lines += [
        "",
        "_Shared-project context is read-only. Your captures stay in this project; "
        "use `/basic-memory:bm-share` to deliberately promote a note to the team._",
    ]
if shared_capped:
    lines += ["", f"_(reading the first {MAX_SHARED} shared projects; more are configured.)_"]

# --- Where to write (placement guidance) ---
# Trigger: a primaryProject is set (so capture is actually active — pre-compact and
# proactive writes land somewhere intentional). Why: the output style tells Claude to
# follow the project's placement conventions, but nothing else surfaces them.
# Outcome: Claude sees that session checkpoints go to captureFolder while decisions/
# tasks/notes follow the stored conventions — so it doesn't dump everything into the
# checkpoint folder. Bounded — conventions are a short string by design.
if primary_project:
    # captureFolder is the PreCompact *checkpoint* folder only; proactive captures
    # (decisions, tasks, notes) follow placementConventions, not this folder.
    placement = [
        "",
        "## Where to write",
        f"- Session checkpoints (the PreCompact auto-capture) go to `{capture_folder}/`.",
    ]
    if placement_conventions:
        placement.append(
            "- Decisions, tasks, and other notes follow these placement "
            f"conventions: {placement_conventions}"
        )
    else:
        placement.append(
            "- Place decisions, tasks, and notes in folders that fit their topic, "
            "not the checkpoint folder."
        )
    lines += placement

# --- First-run / config nudges ---
if not configured:
    lines += ["", setup_nudge]
elif not primary_project:
    lines += [
        "",
        "_Tip: set `basicMemory.primaryProject` in `.claude/settings.json` to "
        "pin this project (see the plugin's settings.example.json)._",
    ]

lines += ["", "---", recall_prompt]
print("\n".join(lines))

# --- Log the session_started event locally (opt-in) ---
# Trigger: captureEvents is enabled and the envelope module is available.
# Why: the local event log records session starts for later coalescing by memory
#      routines (SPEC-61). This is separate from the brief printed above — it's
#      durable metadata, not context for the current session.
# Outcome: a JSONL line is appended to the project's event log under the
#          Basic Memory data dir (never inside the user's repository).
if _HAS_ENVELOPE and capture_events and primary_project:
    try:
        envelope = create_envelope(
            event_type=SESSION_STARTED,
            source="claude-code",
            session_id=session_id or "unknown",
            cwd=cwd,
            project_hint=primary_project,
            hook_name="SessionStart",
        )
        append_to_event_log(envelope)
    except Exception:
        pass  # event logging failure is non-fatal
PY
