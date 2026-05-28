#!/usr/bin/env bash
#
# SessionStart hook — brief Claude from Basic Memory at the start of a session.
#
# This is the read side of the memory bridge: it puts the most relevant slice of
# the durable knowledge graph in front of Claude before the first prompt, so the
# session starts oriented instead of cold.
#
# Phase 1 is the *minimal* cut (see DESIGN.md): a single structured query for
# active tasks, plus the always-on recall prompt. The richer multi-query brief
# (open decisions, recent sessions, team projects) is the "enrich later" step.
#
# Contract: this hook is advisory and must NEVER disrupt a session. Every failure
# path exits 0 with no output. SessionStart adds plain stdout to Claude's context
# (verified — Q4), and output is capped at 10,000 chars, so we keep it small.

set -u

# --- Read the hook payload (stdin is JSON: cwd, source, session_id, ...) ---
# stdin can only be consumed once; capture it before anything else touches it.
input="$(cat 2>/dev/null || true)"

# --- Locate the Basic Memory CLI ---
# Trigger: bm not installed / not on PATH.
# Why: the plugin is useful on its own merits to BM users; for everyone else it
#      must be invisible. No binary → no brief, no error.
# Outcome: silent no-op.
BM="$(command -v basic-memory || command -v bm || true)"
[ -z "$BM" ] && exit 0

# Everything else runs in one Python pass: parse config, run the query, format
# the brief. Python is a guaranteed dependency (basic-memory requires it) and
# avoids brittle shell JSON wrangling. The payload and binary path cross over via
# the environment to sidestep argument-quoting issues.
BM_HOOK_INPUT="$input" BM_BIN="$BM" python3 <<'PY' 2>/dev/null || exit 0
import json
import os
import subprocess
import sys

bm = os.environ.get("BM_BIN", "basic-memory")

# --- Resolve the working directory from the payload ---
try:
    payload = json.loads(os.environ.get("BM_HOOK_INPUT") or "{}")
except Exception:
    payload = {}
cwd = payload.get("cwd") or os.getcwd()


# --- Load plugin config from .claude settings (local overrides committed) ---
# Precedence: settings.local.json (per-user) wins over settings.json (team).
# Bare defaults apply when neither sets a key.
def load_settings(directory):
    # Returns (merged basicMemory block, found) where `found` is True if either
    # settings file declared a basicMemory block at all — that presence is the
    # first-run sentinel (setup writing the block is what stops the nudge below).
    merged = {}
    found = False
    for name in ("settings.json", "settings.local.json"):
        path = os.path.join(directory, ".claude", name)
        try:
            with open(path) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            continue
        except Exception:
            continue
        block = data.get("basicMemory")
        if isinstance(block, dict):
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


# --- Query the graph (single call, best-effort) ---
# Filter-only search for active work. --project is passed only when pinned;
# otherwise the CLI routes to the user's default project, so the hook is useful
# out of the box with zero config.
def search(args):
    cmd = [bm, "tool", "search-notes", *args, "--page-size", "5"]
    if primary_project:
        cmd += ["--project", primary_project]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


# The first-run nudge — shown until setup writes a basicMemory config block.
setup_nudge = (
    "_Basic Memory isn't set up for this project yet. Run "
    "`/basic-memory:setup` (~2 min) to configure session briefings and checkpoints._"
)

try:
    tasks = search(["--type", "task", "--status", "active"])
except Exception:
    tasks = None

# Trigger: the task query couldn't run (no default project yet, transient error).
# Why: a broken query must never error the session — but a genuine first-run user
#      (no config, nothing to brief) should still be pointed at setup.
# Outcome: first-run → nudge only; already-configured → silent no-op.
if tasks is None:
    if not configured:
        print("# Basic Memory\n\n" + setup_nudge)
    sys.exit(0)


def label(result):
    name = result.get("title") or result.get("file_path") or "(untitled)"
    ref = result.get("permalink") or result.get("file_path") or ""
    return f"- {name}" + (f" — {ref}" if ref else "")


# --- Assemble the brief (plain stdout → Claude's context) ---
lines = ["# Basic Memory — session context", ""]
project_label = primary_project or "default project"
results = tasks.get("results") or []

if results:
    lines.append(f"**Project:** {project_label}")
    lines.append("")
    lines.append(f"## Active tasks ({len(results)})")
    lines.extend(label(r) for r in results)
else:
    lines.append(f"**Project:** {project_label} · no active tasks tracked")

# Trigger: no basicMemory config block in either settings file (first run).
# Why: point the user at the one-time setup so the plugin is wired up properly.
# Outcome: a single nudge line; once setup writes the block, this stops firing.
if not configured:
    lines.append("")
    lines.append(setup_nudge)
elif not primary_project:
    lines.append("")
    lines.append(
        "_Tip: set `basicMemory.primaryProject` in `.claude/settings.json` to "
        "pin this project (see the plugin's settings.example.json)._"
    )

lines.append("")
lines.append("---")
lines.append(recall_prompt)

print("\n".join(lines))
PY
