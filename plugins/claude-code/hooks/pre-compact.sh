#!/usr/bin/env bash
#
# PreCompact hook — checkpoint the session to Basic Memory before compaction.
#
# This is the write side of the memory bridge: right before Claude Code compacts
# the context window (and the texture of the session is about to be lost), we
# write a durable SessionNote to the graph so the next session can resume from it.
#
# Phase 1 is the *extractive* cut (see DESIGN.md): we lift the opening request and
# the most recent turns straight from the transcript — no LLM call. Verified (Q2)
# that PreCompact has a ~600s budget, so a real LLM-summarized checkpoint is the
# planned "enrich later" upgrade; extractive is the safe, fast first version.
#
# Contract: advisory, never blocks compaction. Every failure path exits 0. We only
# write when a primaryProject is configured — we never write to a user's default
# graph unless they've explicitly pointed the plugin at a project.

set -u

input="$(cat 2>/dev/null || true)"

# Resolve how to invoke the Basic Memory CLI — prefer an explicit command when the
# host configured one, then a binary on PATH. Fall back to uvx / uv so checkpointing
# still works when BM was connected only as an ephemeral `uvx basic-memory mcp`
# server (no persistent CLI). Silent no-op if none available.
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
from datetime import datetime

# --- Load the harness envelope module (vendored next to this hook) ---
# Trigger: this hook wants to stamp provenance and idempotency on the checkpoint.
# Why: the envelope normalizes hook events so downstream consumers (recall,
#      consolidation, memory routines) can trace where each note came from.
# An installed plugin package is just this plugin directory — plugins/shared/
# does not ship — so scripts/sync_plugin_shared.py vendors the module into
# hooks/ (canonical source: plugins/shared/harness_envelope.py).
# Constraint: __file__ is '<stdin>' inside a bash heredoc, so the hook script's
#             real directory is passed in via the BM_HOOK_DIR environment variable.
_hook_dir = os.environ.get("BM_HOOK_DIR", "")
if _hook_dir:
    sys.path.insert(0, _hook_dir)
try:
    from harness_envelope import (
        COMPACTION_IMMINENT,
        append_to_event_log,
        create_envelope,
        to_frontmatter_fields,
        to_provenance_observations,
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

# A project ref can be a workspace-qualified name (route via --project) or an
# external_id UUID (route via --project-id) — names collide across workspaces, so
# bare names won't route. Mirror session-start.sh's detection.
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

try:
    payload = json.loads(os.environ.get("BM_HOOK_INPUT") or "{}")
except Exception:
    payload = {}

cwd = payload.get("cwd") or os.getcwd()
transcript_path = payload.get("transcript_path") or ""
session_id = payload.get("session_id") or ""


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
    # Same precedence as session-start.sh: user-level ~/.claude/settings.json is
    # the base (no user-level settings.local.json — it isn't a real Claude Code
    # source), then the nearest project .claude (settings.json, then
    # settings.local.json) overrides it. cwd may be a repo subdirectory, so walk
    # ancestors to the project root rather than reading cwd alone.
    merged = {}
    home = os.path.expanduser("~")
    sources = [(home, ("settings.json",))]
    project = _project_dir(directory)  # already absolute
    if project != home:
        sources.append((project, ("settings.json", "settings.local.json")))
    for d, names in sources:
        for name in names:
            block = _read_block(os.path.join(d, ".claude", name))
            if block is not None:
                merged.update(block)
    return merged


cfg = load_settings(cwd)
primary_project = (cfg.get("primaryProject") or "").strip()
capture_folder = (cfg.get("captureFolder") or "sessions").strip()
# Strict boolean: captureEvents is a privacy gate, so it fails closed. A
# hand-edited string like "false" (truthy in Python) must not enable capture.
capture_events = cfg.get("captureEvents") is True
redact_keys = cfg.get("redactKeys") or []
redact_paths = cfg.get("redactPaths") or []
# Approximate retention cap for the local event log; the envelope module
# validates it (non-positive/junk values fall back to its default).
event_retention = cfg.get("eventRetention")

# Trigger: no project pinned for this Claude Code project.
# Why: a checkpoint must land somewhere intentional. Writing to the default graph
#      on every compaction would pollute it without consent.
# Outcome: silent no-op until the user sets basicMemory.primaryProject.
if not primary_project:
    sys.exit(0)


# --- Extract conversation text from the transcript (JSONL) ---
# The transcript is one JSON object per line. Schemas vary across Claude Code
# versions, so we probe a few shapes defensively rather than assume one.
def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def turns(path):
    collected = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                # Skip injected/meta frames and tool results — only real human
                # input and assistant prose count. Claude Code marks tool results
                # with a `toolUseResult` field and injected/meta turns (command
                # wrappers, system reminders, auto-continuations) with `isMeta`.
                # Filtering on those flags — not a "<" content prefix — avoids both
                # dropping legitimate messages that start with "<" and capturing
                # tool-result noise.
                if obj.get("isMeta") or obj.get("toolUseResult") is not None:
                    continue
                msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
                role = msg.get("role") or obj.get("type")
                if role not in ("user", "assistant"):
                    continue
                text = text_of(msg.get("content")).strip()
                if not text:
                    continue
                collected.append((role, text))
    except Exception:
        return []
    return collected


conversation = turns(transcript_path)

# Trigger: nothing usable in the transcript, or no real human turn in it.
# Why: an empty or human-less checkpoint is worse than none — it would write a
#      note with a dangling title and no opening request. Require a user turn.
# Outcome: silent no-op.
if not conversation or not any(role == "user" for role, _ in conversation):
    sys.exit(0)

user_msgs = [t for r, t in conversation if r == "user"]
opening = user_msgs[0] if user_msgs else ""
recent_user = user_msgs[-3:]


def clip(s, n):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# --- Build a schema-conforming SessionNote ---
# Frontmatter carries type/status/started so structured recall (SessionStart) can
# find it with metadata filters. BM merges a leading frontmatter block from the
# content into the note's frontmatter (verified empirically).
now = datetime.now()
iso = now.strftime("%Y-%m-%dT%H:%M")
# Second precision keeps the title — and therefore the note's permalink — unique
# across rapid compactions within the same minute (otherwise the second write
# would collide with the first and be dropped or overwrite it).
title = f"Session {now.strftime('%Y-%m-%d %H:%M:%S')} — {clip(opening, 40)}"

frontmatter = [
    "---",
    "type: session",
    "status: open",
    f"started: {iso}",
    f"ended: {iso}",
    f"project: {primary_project}",
    f"cwd: {cwd}",
]
if session_id:
    frontmatter.append(f"claude_session_id: {session_id}")

# --- Harness envelope: stamp provenance and idempotency onto the checkpoint ---
# Trigger: the shared envelope module is available (always, unless the shared/
#          directory is missing). Why: provenance makes each checkpoint traceable
#          to its source hook, session, and exact event. Idempotency prevents
#          duplicate notes when the hook fires more than once in the same minute.
envelope = None
if _HAS_ENVELOPE:
    try:
        envelope = create_envelope(
            event_type=COMPACTION_IMMINENT,
            source="claude-code",
            session_id=session_id or "unknown",
            cwd=cwd,
            project_hint=primary_project,
            hook_name="PreCompact",
            timestamp=iso,
            payload_summary={"opening": clip(opening, 200)} if opening else {},
            redact_keys=redact_keys,
            redact_paths=redact_paths,
        )
        for key, value in to_frontmatter_fields(envelope).items():
            frontmatter.append(f"{key}: {value}")
    except Exception:
        pass  # envelope creation failure is non-fatal

frontmatter += ["capture: extractive", "---"]

body = [
    "",
    f"# {title}",
    "",
    "_Automatic pre-compaction checkpoint (extractive). Full detail lives in the "
    "session transcript; this note captures the thread so the next session can "
    "resume._",
    "",
    "## Summary",
    f"Working in `{cwd}`.",
    f"- Opening request: {clip(opening, 300)}" if opening else "",
    "",
    "## Recent thread",
]
body += [f"- {clip(m, 200)}" for m in recent_user] or ["- (no recent user messages captured)"]
body += [
    "",
    "## Observations",
    f"- [context] Session opened with: {clip(opening, 200)}" if opening else "- [context] Session checkpointed before compaction",
    "- [next_step] Review this checkpoint and continue where the thread left off",
]

# --- Append envelope provenance observations ---
# These stamp the note with its producer source so downstream consumers can
# trace provenance without storing the full raw event.
if _HAS_ENVELOPE and envelope:
    body += to_provenance_observations(envelope)

# --- Log the event locally for coalescing ---
# Trigger: captureEvents is enabled. Why: the local event log feeds future
# memory routines (SPEC-61) without requiring the note to carry every detail.
if _HAS_ENVELOPE and envelope and capture_events:
    append_to_event_log(envelope, cap=event_retention)

content = "\n".join(frontmatter + body)

# --- Write the checkpoint (best-effort) ---
# A UUID primaryProject must route via --project-id, not --project, or the write
# silently fails to land in a UUID-configured project.
project_flag = "--project-id" if UUID_RE.match(primary_project) else "--project"
try:
    subprocess.run(
        [
            *bm_cmd, "tool", "write-note",
            "--title", title,
            "--folder", capture_folder,
            project_flag, primary_project,
            "--tags", "session",
            "--tags", "auto-capture",
        ],
        input=content,
        capture_output=True,
        text=True,
        timeout=60,
    )
except Exception:
    sys.exit(0)
PY
