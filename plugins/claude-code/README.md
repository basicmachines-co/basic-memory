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

## Commands

Plugin skills are namespaced under the plugin name:

| Command | What it does |
|---------|--------------|
| `/basic-memory:setup` | One-time guided setup — maps the project to a Basic Memory project, seeds the note schemas, optionally learns your conventions, and turns on the capture reflexes. Run this first. |
| `/basic-memory:remember <text>` | Quick capture — saves the text to the `bm-remember` folder with a `manual-capture` tag. Also fires when you say "remember that…". |
| `/basic-memory:share <note>` | Promote a personal note to a configured team project, with attribution and confirmation. The deliberate way to write to a shared workspace. |
| `/basic-memory:status` | Diagnostic — shows the active project, team read-sources and share targets, capture folders, output-style state, recent session checkpoints, and active-task count. |

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

The fastest path is **`/basic-memory:setup`** — a ~2-minute interview that writes
the config, seeds the schemas, and turns on the capture reflexes. The SessionStart
hook nudges you toward it on first run.

To configure by hand instead: the hooks work out of the box against your **default**
Basic Memory project — no config required. To pin a specific project (recommended,
and required for the PreCompact checkpoint to write), add a `basicMemory` block to
your project's `.claude/settings.json`. Copy
[`settings.example.json`](./settings.example.json) and set `primaryProject`:

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

## Teams

If you're on Basic Memory Cloud with a team workspace, the plugin reads team context
into your session brief and gives you a deliberate way to publish back — **without
ever auto-writing to the shared graph.**

- **Read across** — add team projects to `secondaryProjects`. SessionStart pulls their
  open decisions into your brief (in parallel, read-only), so you start oriented on
  what the team has decided.
- **Capture stays personal** — session checkpoints and `/basic-memory:remember` only
  ever write to your `primaryProject`. Nothing lands in a team project automatically.
- **Share deliberately** — `/basic-memory:share` copies a chosen note into a
  `teamProjects` target (with attribution and a confirmation step). That's the only
  path to a shared write.

Because project names repeat across workspaces, team refs must be **workspace-qualified**
(`my-team/notes`) or `external_id` UUIDs — `/basic-memory:setup` fills these in for you
from `list_workspaces`.

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
