#!/usr/bin/env -S uv run --script
"""Brief Codex from Basic Memory at thread start."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# --- Load the shared envelope module (lives two directories up in plugins/shared/) ---
# SessionStart is read-only (no note writes), so the envelope is only used for
# local event logging when captureEvents is enabled.
_shared_dir = str(Path(__file__).resolve().parent.parent.parent / "shared")
sys.path.insert(0, _shared_dir)
try:
    from harness_envelope import (
        SESSION_STARTED,
        append_to_event_log,
        create_envelope,
    )

    _HAS_ENVELOPE = True
except ImportError:
    _HAS_ENVELOPE = False


UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
MAX_SHARED = 6


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


def load_config(directory: Path) -> tuple[dict, bool]:
    path = directory / ".codex" / "basic-memory.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}, False
    except Exception:
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    return data.get("basicMemory", data), True


def project_args(project_ref: str | None) -> list[str]:
    if not project_ref:
        return []
    flag = "--project-id" if UUID_RE.match(project_ref) else "--project"
    return [flag, project_ref]


def search(
    bm_cmd: list[str],
    filters: list[str],
    project_ref: str | None = None,
    timeout: int = 10,
):
    cmd = [*bm_cmd, "tool", "search-notes", *filters, "--page-size", "5"]
    cmd.extend(project_args(project_ref))
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


def rows(result):
    return (result or {}).get("results") or []


def label(result):
    name = result.get("title") or result.get("file_path") or "(untitled)"
    ref = result.get("permalink") or result.get("file_path") or ""
    return f"- {name}" + (f" - {ref}" if ref else "")


def readable(ref):
    return f"{ref[:8]}..." if UUID_RE.match(ref) else ref


def shared_project_refs(cfg: dict, primary_project: str) -> tuple[list[str], bool]:
    secondary = cfg.get("secondaryProjects")
    secondary = secondary if isinstance(secondary, list) else []
    team = cfg.get("teamProjects")
    team = team if isinstance(team, dict) else {}

    shared_refs: list[str] = []
    for ref in list(secondary) + list(team.keys()):
        if isinstance(ref, str) and ref.strip() and ref.strip() != primary_project:
            clean = ref.strip()
            if clean not in shared_refs:
                shared_refs.append(clean)
    shared_capped = len(shared_refs) > MAX_SHARED
    return shared_refs[:MAX_SHARED], shared_capped


def no_context_message(configured: bool, primary_project: str) -> str:
    if not configured:
        return (
            "# Basic Memory for Codex\n\n"
            "_This repo is not configured for Basic Memory yet. Run `Use Basic Memory "
            "for Codex to set up this repo` to map a project, seed schemas, and turn "
            "on Codex checkpoints._"
        )

    project = primary_project or "the default project"
    return (
        "# Basic Memory for Codex\n\n"
        f"_Could not read from `{project}`. Run `Use bm-status` to check the "
        "Basic Memory project mapping._"
    )


def main() -> int:
    bm_cmd = basic_memory_command()
    if not bm_cmd:
        return 0

    payload = parse_payload()
    cwd = Path(payload.get("cwd") or os.getcwd())
    source = payload.get("source") or "startup"

    cfg, configured = load_config(cwd)
    primary_project = str(cfg.get("primaryProject") or "").strip()
    recall_timeframe = str(cfg.get("recallTimeframe") or "7d").strip()
    capture_folder = str(cfg.get("captureFolder") or "codex-sessions").strip()
    placement = str(cfg.get("placementConventions") or "").strip()
    focus = str(cfg.get("focus") or "").strip()
    capture_events = bool(cfg.get("captureEvents", False))
    shared_refs, shared_capped = shared_project_refs(cfg, primary_project)

    active_tasks = ["--type", "task", "--status", "active"]
    open_decisions = ["--type", "decision", "--status", "open"]
    recent_codex = ["--type", "codex_session", "--after_date", recall_timeframe]
    recent_generic = ["--type", "session", "--after_date", recall_timeframe]

    with ThreadPoolExecutor(max_workers=4 + MAX_SHARED) as pool:
        fut_tasks = pool.submit(search, bm_cmd, active_tasks, primary_project or None)
        fut_decisions = pool.submit(search, bm_cmd, open_decisions, primary_project or None)
        fut_codex = pool.submit(search, bm_cmd, recent_codex, primary_project or None)
        fut_sessions = pool.submit(search, bm_cmd, recent_generic, primary_project or None)
        fut_shared = {ref: pool.submit(search, bm_cmd, open_decisions, ref) for ref in shared_refs}
        primary_tasks = fut_tasks.result()
        primary_decisions = fut_decisions.result()
        primary_codex = fut_codex.result()
        primary_sessions = fut_sessions.result()
        shared_results = {ref: fut.result() for ref, fut in fut_shared.items()}

    if primary_tasks is None and primary_decisions is None and primary_codex is None:
        print(no_context_message(configured, primary_project))
        return 0

    lines = ["# Basic Memory for Codex", ""]
    header = f"Project: {primary_project or 'default project'}"
    if focus:
        header += f" | focus: {focus}"
    if shared_refs:
        header += f" | reading {len(shared_refs)} shared project(s)"
    lines.append(header)
    lines.append(f"Session source: {source}")

    task_rows = rows(primary_tasks)
    decision_rows = rows(primary_decisions)
    codex_rows = rows(primary_codex)
    session_rows = rows(primary_sessions)

    if task_rows:
        lines += ["", f"## Active Tasks ({len(task_rows)})", *[label(r) for r in task_rows]]
    if decision_rows:
        lines += [
            "",
            f"## Open Decisions ({len(decision_rows)})",
            *[label(r) for r in decision_rows],
        ]
    if codex_rows:
        lines += [
            "",
            f"## Recent Codex Checkpoints ({len(codex_rows)})",
            *[label(r) for r in codex_rows],
        ]
    elif session_rows:
        lines += [
            "",
            f"## Recent Sessions ({len(session_rows)})",
            *[label(r) for r in session_rows],
        ]

    shared_sections = [(ref, rows(shared_results.get(ref))) for ref in shared_refs]
    shared_sections = [(ref, items) for ref, items in shared_sections if items]
    if shared_sections:
        lines += ["", "## Shared Context (Read Only)"]
        for ref, items in shared_sections:
            lines += [f"### {readable(ref)} open decisions", *[label(r) for r in items]]
    if shared_capped:
        lines += ["", f"Only the first {MAX_SHARED} shared projects are read on session start."]

    if not (task_rows or decision_rows or codex_rows or session_rows or shared_sections):
        lines += ["", "_No active tasks, open decisions, or recent checkpoints found._"]

    lines += [
        "",
        "## Codex Memory Posture",
        "- Search Basic Memory before answering questions about prior decisions or status.",
        "- Capture durable engineering decisions as typed decision notes.",
        f"- Put automatic Codex checkpoints in `{capture_folder}/`.",
    ]
    if placement:
        lines.append(f"- Follow these placement conventions for other notes: {placement}")
    else:
        lines.append("- Place other notes by topic, not in the checkpoint folder.")

    lines += [
        "",
        "Use Basic Memory as durable context, but keep required repo rules in AGENTS.md "
        "or checked-in docs.",
    ]

    print("\n".join(lines))

    # --- Log the session_started event locally (opt-in) ---
    # Trigger: captureEvents is enabled and the envelope module is available.
    # Why: the local event log records session starts for later coalescing by
    #      memory routines (SPEC-61). This is separate from the brief printed
    #      above — it's durable metadata, not context for the current session.
    # Outcome: a JSONL line is appended to the project's event log under the
    #          Basic Memory data dir (never inside the user's repository).
    if _HAS_ENVELOPE and capture_events and primary_project:
        try:
            session_id = payload.get("session_id") or ""
            envelope = create_envelope(
                event_type=SESSION_STARTED,
                source="codex",
                session_id=session_id or "unknown",
                cwd=str(cwd),
                project_hint=primary_project,
                hook_name="SessionStart",
            )
            append_to_event_log(envelope)
        except Exception:
            pass  # event logging failure is non-fatal

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
