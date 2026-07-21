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
  general `codex_session` notes or schema-backed `coding_session` notes with
  structured repository and pull-request context.
- **Capture decisions.** The `bm-decide` skill records durable engineering
  decisions with rationale, alternatives, and consequences.
- **Remember lightly.** The `bm-remember` skill saves small facts without turning
  them into a full decision or session note.
- **Write useful memory.** The `bm-writing` skill provides one user-customizable
  standard for the voice, narrative quality, observations, and relations used by
  the plugin's note-writing skills.
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

The hook scripts carry no logic: the brief, the checkpoint, and lifecycle-event
capture all live in the pinned Basic Memory revision behind `bm hook`. Each is
a self-contained PEP 723 script pinned to a Basic Memory Git ref. Both refs
are updated together with `just set-codex-hook-version <sha-or-tag>`.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — required: the hooks are PEP 723
  scripts executed via `uv run --script`, which installs their pinned Basic
  Memory revision. Install per platform:
  - macOS: `brew install uv` (or the curl installer below)
  - Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

Disclosure: uv installs the pinned Basic Memory Git ref on first run and reuses
its cache afterward. Every failure path exits 0 — the hooks never disrupt a
session.

## Install

Install the plugin once from the Basic Memory repository root:

```bash
codex plugin marketplace add "$(git rev-parse --show-toplevel)"
codex plugin add codex@basic-memory
```

Plugin installation is user-level in Codex, so one install makes the plugin
available across projects on the same machine. Start a new Codex thread after
installing so Codex can load the plugin skills, MCP configuration, and hooks.

Configuration can live at user level in `~/.codex/basic-memory.json` or at
project level in `.codex/basic-memory.json`. User-level settings are the base;
the nearest project file overrides only the keys it declares. `redactKeys` and
`redactPaths` are the privacy exception: their user and project lists accumulate.
The setup skill asks which scope to use and recommends user-level configuration
by default.

To customize how Codex writes memory, edit `skills/bm-writing/SKILL.md` in the
plugin source. `bm-checkpoint`, `bm-decide`, and `bm-remember` all apply that
shared skill while retaining their own schemas and evidence requirements.

## Configuration

Run the setup skill, or create `~/.codex/basic-memory.json` for shared defaults:

```json
{
  "basicMemory": {
    "primaryProject": "my-project",
    "secondaryProjects": [],
    "teamProjects": {},
    "focus": "code/dev",
    "sessionProfile": "coding",
    "rememberFolder": "codex-remember",
    "recallTimeframe": "7d",
    "captureEvents": true,
    "redactKeys": [],
    "redactPaths": [],
    "placementConventions": "Put decisions in decisions/ and work checkpoints in codex/<repo-dir>/."
  }
}
```

Codex event capture is on by default. Set the JSON boolean `false` at user or
project level to opt out; malformed values fail closed. Captured, redacted
lifecycle-event envelopes land in a local inbox under your Basic Memory home
(`basic-memory hook status` / `basic-memory hook flush`). Add `redactKeys` and
`redactPaths` arrays to extend the built-in redaction floor.

When `captureFolder` is omitted, Codex resolves the Git top-level directory and
writes to `codex/<repo-dir>`. An explicit folder still wins.

For a coding profile, keep the checkout-specific repository identifier in the
project file without duplicating the shared settings:

```json
{
  "basicMemory": {
    "repository": "owner/repo"
  }
}
```

The plugin's seed schemas cover notes Codex writes directly: `codex_session`,
`coding_session`, `decision`, and `task`. Coding sessions require structured
repository, repository-root, working-directory, branch, and Git SHA frontmatter;
current pull-request fields are added when a PR exists. Optional flush projection
also writes normalized `session` and `tool_ledger` artifacts. Those are
core-owned contracts implemented and tested with the projector, not duplicate
schema files maintained by each host plugin. `bm-orient` and `bm-status` still
recall normalized `session` notes
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
