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
- **Report status.** The `bm-status` skill shows configuration, reachability, and
  recent memory state.

## Package Contents

| Path | Role |
| --- | --- |
| `.codex-plugin/plugin.json` | Codex plugin manifest |
| `.mcp.json` | Basic Memory MCP server configuration |
| `hooks/hooks.json` | SessionStart and PreCompact hook registration |
| `hooks/session-start.sh` | Shim: execs `basic-memory hook session-start --harness codex` |
| `hooks/pre-compact.sh` | Shim: execs `basic-memory hook pre-compact --harness codex` |
| `skills/` | Codex-native Basic Memory workflows |
| `schemas/` | Seed schemas for Codex sessions, decisions, and tasks |

The hook shims carry no logic: the brief, the checkpoint, and opt-in event
capture all live in the released `basic-memory` package behind `bm hook`.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — the documented prerequisite for the hooks'
  fallback path. Install per platform:
  - macOS: `brew install uv` (or the curl installer below)
  - Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- [Basic Memory](https://github.com/basicmachines-co/basic-memory). `uv tool
  install basic-memory` is recommended (a `basic-memory` binary on PATH keeps the
  hook version consistent with your MCP server).

Disclosure: the shims resolve the CLI as `BM_BIN` → `basic-memory` / `bm` on
PATH → `uvx "basic-memory>=<floor>"`. The uvx fallback fetches the package from
PyPI on first run (pinned minimum version, bumped by release tooling); later
runs use uv's cache. If nothing is resolvable the shims exit silently.

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
    "placementConventions": "Put decisions in decisions/ and work checkpoints in codex-sessions/."
  }
}
```

`captureEvents` is opt-in and off by default: only the JSON boolean `true`
enables recording of redacted lifecycle-event envelopes to a local inbox under
your Basic Memory home (`basic-memory hook status` / `basic-memory hook flush`).

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
