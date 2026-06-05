#!/usr/bin/env bash
#
# PreCompact hook - checkpoint Codex work into Basic Memory before compaction.
#
# Contract: best effort. The hook only writes when .codex/basic-memory.json pins a
# primary project, and every failure exits 0 so compaction can continue.

set -u

input="$(cat 2>/dev/null || true)"

if command -v basic-memory >/dev/null 2>&1; then
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

BM_HOOK_INPUT="$input" BM_BIN="$BM" python3 <<'PY' 2>/dev/null || exit 0
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

bm_cmd = shlex.split(os.environ.get("BM_BIN") or "basic-memory")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

try:
    payload = json.loads(os.environ.get("BM_HOOK_INPUT") or "{}")
except Exception:
    payload = {}

cwd = Path(payload.get("cwd") or os.getcwd())
transcript_path = payload.get("transcript_path") or ""
session_id = payload.get("session_id") or ""
turn_id = payload.get("turn_id") or ""
trigger = payload.get("trigger") or ""
model = payload.get("model") or ""


def load_config(directory: Path) -> dict:
    path = directory / ".codex" / "basic-memory.json"
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data.get("basicMemory", data)


cfg = load_config(cwd)
primary_project = str(cfg.get("primaryProject") or "").strip()
capture_folder = str(cfg.get("captureFolder") or "codex-sessions").strip()

if not primary_project:
    sys.exit(0)


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def transcript_turns(path: str):
    collected = []
    if not path:
        return collected
    try:
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("isMeta") or obj.get("toolUseResult") is not None:
                    continue
                msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
                role = msg.get("role") or obj.get("type")
                if role not in ("user", "assistant"):
                    continue
                text = text_of(msg.get("content")).strip()
                if text:
                    collected.append((role, text))
    except Exception:
        return []
    return collected


def git_status(directory: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "status", "--short"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()][:20]


def clip(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "..."


conversation = transcript_turns(transcript_path)
if not conversation or not any(role == "user" for role, _ in conversation):
    sys.exit(0)

user_messages = [text for role, text in conversation if role == "user"]
assistant_messages = [text for role, text in conversation if role == "assistant"]
opening = user_messages[0] if user_messages else ""
recent_user = user_messages[-3:]
recent_assistant = assistant_messages[-2:]
status_lines = git_status(cwd)

now = datetime.now(timezone.utc)
iso = now.isoformat(timespec="seconds")
title = f"Codex session {now.strftime('%Y-%m-%d %H:%M:%S')} - {clip(opening, 40)}"

frontmatter = [
    "---",
    "type: codex_session",
    "status: open",
    f"started: {iso}",
    f"ended: {iso}",
    f"project: {primary_project}",
    f"cwd: {cwd}",
]
if session_id:
    frontmatter.append(f"codex_session_id: {session_id}")
if turn_id:
    frontmatter.append(f"codex_turn_id: {turn_id}")
if trigger:
    frontmatter.append(f"trigger: {trigger}")
if model:
    frontmatter.append(f"model: {model}")
frontmatter += ["capture: extractive", "---"]

body = [
    "",
    f"# {title}",
    "",
    "_Automatic Codex pre-compaction checkpoint. It records the working cursor, "
    "not a polished summary._",
    "",
    "## Summary",
    f"Working in `{cwd}`.",
    f"- Opening request: {clip(opening, 300)}" if opening else "",
    "",
    "## Recent User Cursor",
]
body += [f"- {clip(message, 240)}" for message in recent_user]
if recent_assistant:
    body += ["", "## Recent Assistant Notes"]
    body += [f"- {clip(message, 240)}" for message in recent_assistant]
if status_lines:
    body += ["", "## Working Tree"]
    body += [f"- `{line}`" for line in status_lines]
body += [
    "",
    "## Observations",
    f"- [context] Codex worked in `{cwd}`",
    f"- [context] Session opened with: {clip(opening, 200)}" if opening else "",
    "- [next_step] Re-read this checkpoint, inspect the current worktree, and continue from the latest user request",
]

content = "\n".join(frontmatter + body)
project_flag = "--project-id" if UUID_RE.match(primary_project) else "--project"

try:
    subprocess.run(
        [
            *bm_cmd,
            "tool",
            "write-note",
            "--title",
            title,
            "--folder",
            capture_folder,
            project_flag,
            primary_project,
            "--tags",
            "codex",
            "--tags",
            "auto-capture",
        ],
        input=content,
        capture_output=True,
        text=True,
        timeout=60,
    )
except Exception:
    sys.exit(0)
PY
