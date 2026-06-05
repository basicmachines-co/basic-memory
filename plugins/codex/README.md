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
| `hooks/session-start.sh` | Injects a compact memory brief at thread start |
| `hooks/pre-compact.sh` | Writes an automatic Codex checkpoint before compaction |
| `skills/` | Codex-native Basic Memory workflows |
| `schemas/` | Seed schemas for Codex sessions, decisions, and tasks |

## Configuration

Plugin installation is user-level in Codex. Install `codex@basic-memory-local`
once and Codex can load the plugin across projects on the same machine.
The `.codex/basic-memory.json` file is still per repository: it maps that
checkout to the Basic Memory project and folders the hooks should use.

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
