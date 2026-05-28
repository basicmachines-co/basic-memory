# Basic Memory for Claude Code

The bridge between **Claude's working memory** and **[Basic Memory](https://basicmemory.com)'s durable knowledge graph**.

Claude Code now keeps its own auto-memory — fast, in-context notes Claude writes
to itself. Basic Memory is the other half: a searchable, portable, semantic graph
of markdown files you and Claude both own. This plugin connects the two so you get
the [documented "use both" setup](https://docs.basicmemory.com/concepts/vs-built-in-memory)
automatically: Claude starts each session briefed from the graph, and checkpoints
the session back to it before the context window compacts.

> This package lives in the canonical [`basic-memory`](https://github.com/basicmachines-co/basic-memory)
> repository under `plugins/claude-code/` and only works with Claude Code. For
> framework-agnostic skills that work in any MCP agent, see the top-level
> [`skills/`](../../skills) directory.

## What it does

- **Session briefing (SessionStart hook).** When a session begins, the plugin
  queries Basic Memory for your active tasks and recent work and puts a short
  brief in front of Claude — so you start where you left off instead of cold.
- **Compaction checkpoint (PreCompact hook).** Right before Claude Code compacts
  the context window, the plugin writes a `type: session` checkpoint note to the
  graph, so the texture of the session survives and the next one can resume from
  it.
- **Capture reflexes (output style).** An opt-in output style teaches Claude to
  search the graph before answering recall questions, capture real decisions as
  typed `decision` notes, and cite permalinks.
- **Seed schemas.** Picoschema definitions for `session`, `decision`, and `task`
  notes, so the stuff the plugin writes is structured and findable by
  `search_notes` metadata filters — recall is precise, not fuzzy.

The full design and rationale live in [DESIGN.md](./DESIGN.md).

## Requirements

- [Basic Memory](https://github.com/basicmachines-co/basic-memory) `>= 0.19.0`
  installed and configured as an MCP server (`uv tool install basic-memory`).
- Claude Code.

## Installation

```bash
claude plugin marketplace add basicmachines-co/basic-memory --sparse .claude-plugin plugins/claude-code
claude plugin install basic-memory@basicmachines-co
```

## Configuration

The hooks work out of the box against your **default** Basic Memory project — no
config required. To pin a specific project (recommended, and required for the
PreCompact checkpoint to write), add a `basicMemory` block to your project's
`.claude/settings.json`. Copy [`settings.example.json`](./settings.example.json)
and set `primaryProject`:

```json
{
  "basicMemory": {
    "primaryProject": "my-project",
    "captureFolder": "sessions"
  }
}
```

To enable the capture reflexes, also set `"outputStyle": "basic-memory"` in your
settings (or select it via `/config`).

| Key | Default | What it does |
|-----|---------|--------------|
| `primaryProject` | _(default project)_ | Where briefs read from and checkpoints write to |
| `captureFolder` | `sessions` | Folder for PreCompact checkpoint notes |
| `recallTimeframe` | `3d` | Recency window for the session brief |
| `recallPrompt` | _(built-in)_ | The instruction appended to the brief |
| `preCompactCapture` | `extractive` | How checkpoints are produced |

See [DESIGN.md](./DESIGN.md) for the complete configuration schema, the
Claude-Code-project ↔ Basic-Memory-project mapping, and team-workspace behavior.

## Development

From the monorepo root:

```bash
just package-check-claude-code
```

From this directory:

```bash
just check
```

`just check` validates the manifests, hooks, output style, and seed schemas, then
runs `claude plugin validate . --strict`.

## License

MIT
