#!/usr/bin/env -S uv run --script
"""Checkpoint Codex work into Basic Memory before compaction."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Load the harness envelope module (vendored next to this hook) ---
# Trigger: this hook wants to stamp provenance and idempotency on the checkpoint.
# Why: the envelope normalizes hook events so downstream consumers (recall,
#      consolidation, memory routines) can trace where each note came from.
# An installed plugin package is just plugins/codex/ — plugins/shared/ does not
# ship — so scripts/sync_plugin_shared.py vendors the module into hooks/
# (canonical source: plugins/shared/harness_envelope.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
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


UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def basic_memory_command() -> list[str] | None:
    configured = os.environ.get("BM_BIN")
    if configured:
        return shlex.split(configured)
    if shutil.which("basic-memory"):
        return ["basic-memory"]
    if shutil.which("bm"):
        return ["bm"]
    if shutil.which("uvx"):
        return ["uvx", "basic-memory"]
    if shutil.which("uv"):
        return ["uv", "tool", "run", "basic-memory"]
    return None


def parse_payload() -> dict:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_config(directory: Path) -> dict:
    path = directory / ".codex" / "basic-memory.json"
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data.get("basicMemory", data)


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


def main() -> int:
    bm_cmd = basic_memory_command()
    if not bm_cmd:
        return 0

    payload = parse_payload()
    cwd = Path(payload.get("cwd") or os.getcwd())
    transcript_path = payload.get("transcript_path") or ""
    session_id = payload.get("session_id") or ""
    turn_id = payload.get("turn_id") or ""
    trigger = payload.get("trigger") or ""
    model = payload.get("model") or ""

    cfg = load_config(cwd)
    primary_project = str(cfg.get("primaryProject") or "").strip()
    capture_folder = str(cfg.get("captureFolder") or "codex-sessions").strip()
    # Strict boolean: captureEvents is a privacy gate, so it fails closed. A
    # hand-edited string like "false" (truthy in Python) must not enable capture.
    capture_events = cfg.get("captureEvents") is True
    redact_keys = cfg.get("redactKeys") or []
    redact_paths_cfg = cfg.get("redactPaths") or []
    # Approximate retention cap for the local event log; the envelope module
    # validates it (non-positive/junk values fall back to its default).
    event_retention = cfg.get("eventRetention")

    if not primary_project:
        return 0

    conversation = transcript_turns(transcript_path)
    if not conversation or not any(role == "user" for role, _ in conversation):
        return 0

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
                source="codex",
                session_id=session_id or "unknown",
                cwd=str(cwd),
                project_hint=primary_project,
                hook_name="PreCompact",
                turn_id=turn_id or None,
                timestamp=iso,
                payload_summary={
                    "opening": clip(opening, 200),
                    "trigger": trigger,
                }
                if opening
                else {"trigger": trigger},
                redact_keys=redact_keys,
                redact_paths=redact_paths_cfg,
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
        "- [next_step] Re-read this checkpoint, inspect the current worktree, and "
        "continue from the latest user request",
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
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
