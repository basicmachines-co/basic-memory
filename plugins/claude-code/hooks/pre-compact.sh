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

BM="$(command -v basic-memory || command -v bm || true)"
[ -z "$BM" ] && exit 0

BM_HOOK_INPUT="$input" BM_BIN="$BM" python3 <<'PY' 2>/dev/null || exit 0
import json
import os
import re
import subprocess
import sys
from datetime import datetime

bm = os.environ.get("BM_BIN", "basic-memory")

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


def load_settings(directory):
    merged = {}
    for name in ("settings.json", "settings.local.json"):
        path = os.path.join(directory, ".claude", name)
        try:
            with open(path) as fh:
                merged.update(json.load(fh).get("basicMemory") or {})
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return merged


cfg = load_settings(cwd)
primary_project = (cfg.get("primaryProject") or "").strip()
capture_folder = (cfg.get("captureFolder") or "sessions").strip()

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

content = "\n".join(frontmatter + body)

# --- Write the checkpoint (best-effort) ---
# A UUID primaryProject must route via --project-id, not --project, or the write
# silently fails to land in a UUID-configured project.
project_flag = "--project-id" if UUID_RE.match(primary_project) else "--project"
try:
    subprocess.run(
        [
            bm, "tool", "write-note",
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
