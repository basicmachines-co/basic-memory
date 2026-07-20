# Basic Memory for Codex

Basic Memory for Codex is the Codex-native bridge between a working coding thread
and Basic Memory's durable knowledge graph.

It is not a 1:1 copy of the Claude Code plugin. This version leans into Codex
workflows: repo orientation, long-running goals, changed-file evidence, explicit
verification, decision capture, and resumable checkpoints.

## What It Does

- **Orient from memory.** The `bm-orient` skill reads active tasks, open
  decisions, and recent Codex checkpoints before substantial work.
- **Checkpoint work.** The `bm-checkpoint` skill and `PreCompact` hook write
  `type: codex_session` notes with the current work cursor.
- **Capture decisions.** The `bm-decide` skill records durable engineering
  decisions with rationale, alternatives, and consequences.
- **Remember lightly.** The `bm-remember` skill saves small facts without turning
  them into a full decision or session note.
- **Share deliberately.** The `bm-share` skill copies personal notes to configured
  team projects only after confirmation.
- **Report status.** The `bm-status` skill shows configuration, reachability,
  shared local hook inbox/flush health, and recent memory state.

## Package Contents

| Path | Role |
| --- | --- |
| `.codex-plugin/plugin.json` | Codex plugin manifest |
| `.mcp.json` | Basic Memory MCP server configuration |
| `hooks/hooks.json` | SessionStart and PreCompact hook registration |
| `hooks/session_start.py` | uv script: runs `basic-memory hook session-start --harness codex` |
| `hooks/pre_compact.py` | uv script: runs `basic-memory hook pre-compact --harness codex` |
| `skills/` | Codex-native Basic Memory workflows |
| `schemas/` | Seed schemas for Codex sessions, decisions, and tasks |

The hook scripts carry no logic: the brief, the checkpoint, and opt-in event
capture all live in the released `basic-memory` package behind `bm hook`. Each
is a self-contained PEP 723 script whose inline metadata pins the released
dependency floor uv resolves.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — required: the hooks are PEP 723
  scripts executed via `uv run --script`. Install per platform:
  - macOS: `brew install uv` (or the curl installer below)
  - Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- [Basic Memory](https://github.com/basicmachines-co/basic-memory). `uv tool
  install basic-memory` is recommended (a `basic-memory` binary on PATH keeps the
  hook version consistent with your MCP server).

Disclosure: uv resolves `basic-memory>=<floor>` from each script's inline
metadata, fetching from PyPI on first run (pinned minimum version, bumped by
release tooling); later runs use uv's cache. `BM_BIN` (a binary path or a
quoted launcher string) overrides the uv-managed environment for development.
Every failure path exits 0 — the hooks never disrupt a session.

## Install

Install the plugin once from the Basic Memory repository root:

```bash
codex plugin marketplace add "$(git rev-parse --show-toplevel)"
codex plugin add codex@basic-memory-local
```

Plugin installation is user-level in Codex, so one install makes the plugin
available across projects on the same machine. Start a new Codex thread after
installing so Codex can load the plugin skills, MCP configuration, and hooks.

Each repository still needs its own `.codex/basic-memory.json` so the plugin
knows which Basic Memory project and folders to use for that checkout. Run the
setup skill in each repo, or create the config file shown below.

## Configuration

Run the setup skill, or create `.codex/basic-memory.json` in a repo:

```json
{
  "basicMemory": {
    "primaryProject": "my-project",
    "secondaryProjects": [],
    "teamProjects": {},
    "focus": "code/dev",
    "captureFolder": "codex-sessions",
    "rememberFolder": "codex-remember",
    "recallTimeframe": "7d",
    "captureEvents": false,
    "redactKeys": [],
    "redactPaths": [],
    "placementConventions": "Put decisions in decisions/ and work checkpoints in codex-sessions/."
  }
}
```

`captureEvents` is opt-in and off by default: only the JSON boolean `true`
enables recording of redacted lifecycle-event envelopes to a local inbox under
your Basic Memory home (`basic-memory hook status` / `basic-memory hook flush`).
Add `redactKeys` and `redactPaths` arrays to extend the built-in redaction floor
for repository-specific payload fields and paths.

The plugin's seed schemas cover notes Codex writes directly: `codex_session`,
`decision`, and `task`. Optional flush projection also writes normalized
`session` and `tool_ledger` artifacts. Those are core-owned contracts implemented
and tested with the projector, not duplicate schema files maintained by each host
plugin. `bm-orient` and `bm-status` still recall normalized `session` notes
alongside Codex checkpoints.

Codex plugin hooks must be reviewed and trusted before they run. Open `/hooks` in
Codex after enabling the plugin and trust the Basic Memory hook definitions.

## Development

From this directory:

```bash
just check
```

From the repo root:

```bash
just package-check-codex
```

The package intentionally keeps Codex-specific configuration separate from
Claude's `.claude/settings.json`.
