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
| `/basic-memory:bm-setup` | One-time guided setup — maps the project to a Basic Memory project, seeds the note schemas, installs the shared `memory-*` skills, optionally learns your conventions, and turns on the capture reflexes. Run this first. |
| `/basic-memory:bm-remember <text>` | Quick capture — saves the text to the `bm-remember` folder with a `manual-capture` tag. Also fires when you say "remember that…". |
| `/basic-memory:bm-share <note>` | Promote a personal note to a configured team project, with attribution and confirmation. The deliberate way to write to a shared workspace. |
| `/basic-memory:bm-status` | Diagnostic — shows the active project, team read-sources and share targets, capture folders, output-style state, recent session checkpoints, and active-task count. |

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — required: the hooks are PEP 723
  scripts executed via `uv run --script`. Install per platform:
  - macOS: `brew install uv` (or the curl installer below)
  - Linux/macOS: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- [Basic Memory](https://github.com/basicmachines-co/basic-memory) connected as an
  MCP server. The hooks resolve their own `basic-memory` through uv, so no
  PATH install is required for them.
- Claude Code.

### What the hooks execute

The hook scripts are zero-logic PEP 723 uv scripts: `uv run --quiet --script`
resolves `basic-memory>=<floor>` (pinned in each script's inline metadata) and
the script invokes `basic-memory hook <event>` in-process with the hook JSON
on stdin. All behavior lives in the released Python package — versioned,
typed, and tested. Setting `BM_BIN` (a binary path or a quoted launcher
string) overrides the uv-managed environment, e.g. for development against a
local checkout. Two disclosures:

- **Network fetch on first run.** uv downloads `basic-memory` from PyPI at a
  pinned minimum version (bumped by release tooling); later runs use uv's
  cache.
- **Event capture is opt-in and off by default.** Setting `captureEvents: true`
  (the JSON boolean — strings never enable it) records redacted lifecycle-event
  envelopes to a local inbox under your Basic Memory home. Inspect with
  `basic-memory hook status`, project with `basic-memory hook flush`.

Every failure path exits 0 — the hooks stay invisible rather than disrupt a
session.

## Installation

```bash
claude plugin marketplace add basicmachines-co/basic-memory --sparse .claude-plugin plugins/claude-code
claude plugin install basic-memory@basicmachines-co
```

## Configuration

The fastest path is **`/basic-memory:bm-setup`** — a ~2-minute interview that writes
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

The block can also live in your **user-level** `~/.claude/settings.json` — one
block there covers every project, no per-repo setup. Precedence, lowest to
highest: user-level `settings.json` → project `settings.json` → project
`settings.local.json`, merged per key — so a project that pins its own
`primaryProject` wins over the user-level default. (This mirrors Claude Code's
own sources: `settings.local.json` is project-scoped only, so there is no
user-level `settings.local.json`.) The hooks resolve the project settings from
the nearest ancestor directory (including the working directory) whose `.claude`
folder contains a `settings.json` or `settings.local.json`, so a mapping in the
repo root still applies when Claude starts in a subdirectory.

To enable the capture reflexes, also set `"outputStyle": "basic-memory"` in your
settings (or select it via `/config`).

| Key | Default | What it does |
|-----|---------|--------------|
| `primaryProject` | _(default project)_ | Where briefs read from and checkpoints write to |
| `captureFolder` | `sessions` | Folder for PreCompact checkpoint notes |
| `recallTimeframe` | `3d` | Recency window for the session brief |
| `recallPrompt` | _(built-in)_ | The instruction appended to the brief |
| `preCompactCapture` | `extractive` | How checkpoints are produced |
| `captureEvents` | `false` | Opt-in: record redacted lifecycle-event envelopes to the local inbox (see `basic-memory hook status` / `flush`). Only the JSON boolean `true` enables it. |

See [DESIGN.md](./DESIGN.md) for the complete configuration schema, the
Claude-Code-project ↔ Basic-Memory-project mapping, and team-workspace behavior.

## Teams

If you're on Basic Memory Cloud with a team workspace, the plugin reads team context
into your session brief and gives you a deliberate way to publish back — **without
ever auto-writing to the shared graph.**

- **Read across** — add team projects to `secondaryProjects`. SessionStart pulls their
  open decisions into your brief (in parallel, read-only), so you start oriented on
  what the team has decided.
- **Capture stays personal** — session checkpoints and `/basic-memory:bm-remember` only
  ever write to your `primaryProject`. Nothing lands in a team project automatically.
- **Share deliberately** — `/basic-memory:bm-share` copies a chosen note into a
  `teamProjects` target (with attribution and a confirmation step). That's the only
  path to a shared write.

Because project names repeat across workspaces, team refs must be **workspace-qualified**
(`my-team/notes`) or `external_id` UUIDs — `/basic-memory:bm-setup` fills these in for you
from `list_workspaces`.

## Documentation

- [Why combine Basic Memory with Claude's built-in memory](./docs/why-combine-memory.md) — the value, the personas, the use cases.
- [Getting started](./docs/getting-started.md) — a ~5-minute walkthrough from install to a working memory loop.
- [Architecture](./docs/architecture.md) — how it works, flow by flow, with diagrams.
- [DESIGN.md](./DESIGN.md) — design rationale, decisions, and roadmap.

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
