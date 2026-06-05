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
| `hooks/session-start.sh` | Launches the SessionStart uv script |
| `hooks/session-start.py` | Injects a compact memory brief at thread start |
| `hooks/pre-compact.sh` | Launches the PreCompact uv script |
| `hooks/pre-compact.py` | Writes an automatic Codex checkpoint before compaction |
| `skills/` | Codex-native Basic Memory workflows |
| `schemas/` | Seed schemas for Codex sessions, decisions, and tasks |

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
    "placementConventions": "Put decisions in decisions/ and work checkpoints in codex-sessions/."
  }
}
```

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
