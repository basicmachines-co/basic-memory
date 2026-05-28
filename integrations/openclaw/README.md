# openclaw-basic-memory

Give your OpenClaw agent persistent, searchable memory — in plain text files you can read and edit.

## What is Basic Memory?

[Basic Memory](https://basicmemory.com) stores AI knowledge in local Markdown files and indexes them into a semantic knowledge graph. Your agent writes notes. You can open them in any editor, read them, change them, and the changes sync back automatically. No black box. No proprietary format. Just files.

It does three things that work together:

- **Stores knowledge in plain Markdown** — everything lives in plain text files on your computer, not locked inside a database you can't read
- **Creates connections automatically** — notes link to each other through a searchable, traversable knowledge graph
- **Searches by meaning, not just keywords** — vector search finds relevant context even when the exact words don't match
- **Keeps notes consistent** — dynamic schemas and validation ensure your knowledge base stays structured and useful as it grows
- **Enables two-way collaboration** — both you and the AI read and write the same files

Over time, your agent builds a knowledge base that grows with you. Context that survives across sessions. Memory that belongs to you.

Learn more: [basicmemory.com](https://basicmemory.com) · [GitHub](https://github.com/basicmachines-co/basic-memory) · [Docs](https://docs.basicmemory.com)

Source now lives in the canonical [`basic-memory`](https://github.com/basicmachines-co/basic-memory) repository under `integrations/openclaw/`. The npm package remains `@basicmemory/openclaw-basic-memory`.

Maintainer check from the monorepo root:

```bash
just package-check-openclaw
```

## What this plugin does

This plugin connects Basic Memory to OpenClaw so your agent can:

- **Remember across sessions** — search and recall past conversations, decisions, and context
- **Track work in progress** — structured tasks that survive context compaction
- **Build knowledge over time** — notes, observations, and relations that grow into a connected graph
- **Search intelligently** — composited search across working memory, the knowledge graph, and active tasks in parallel

All data stays on your machine as Markdown files indexed locally with SQLite. Cloud sync is available but entirely optional.

## Install

**Prerequisite:** [uv](https://docs.astral.sh/uv/) (Python package manager) — used to install the Basic Memory CLI.

```bash
# macOS
brew install uv

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install the plugin:

```bash
openclaw plugins install @basicmemory/openclaw-basic-memory
openclaw plugins enable openclaw-basic-memory --slot memory
openclaw gateway restart
```

That's it. The plugin auto-installs the `bm` CLI on first startup if it's not already on your PATH. See [SECURITY.md](./SECURITY.md) for details on how this works.

Verify:

```bash
openclaw plugins list
openclaw plugins inspect openclaw-basic-memory --json
openclaw plugins doctor
```

## Configuration

### Zero-config (recommended)

```json5
{
  plugins: {
    entries: {
      "openclaw-basic-memory": {
        enabled: true
      }
    },
    slots: {
      memory: "openclaw-basic-memory"
    }
  }
}
```

This uses sensible defaults: auto-generated project name, maps to your workspace root, captures conversations, and recalls active tasks on session start.

### Full options

```json5
{
  plugins: {
    entries: {
      "openclaw-basic-memory": {
        enabled: true,
        config: {
          project: "my-agent",        // BM project name (default: "openclaw-{hostname}")
          projectPath: ".",            // Project directory (default: workspace root)
          memoryDir: "memory/",        // Where task notes live
          memoryFile: "MEMORY.md",     // Working memory file
          autoCapture: true,           // Record conversations as daily notes
          autoRecall: true,            // Inject active tasks + recent activity at session start
          debug: false                 // Verbose logging
        }
      }
    },
    slots: {
      memory: "openclaw-basic-memory"
    }
  }
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `project` | `"openclaw-{hostname}"` | Basic Memory project name |
| `bmPath` | `"bm"` | Path to BM CLI binary |
| `projectPath` | `"."` | Project data directory |
| `memoryDir` | `"memory/"` | Relative path for task scanning |
| `memoryFile` | `"MEMORY.md"` | Working memory file for text search |
| `autoCapture` | `true` | Auto-index agent conversations |
| `captureMinChars` | `10` | Min chars to trigger capture |
| `autoRecall` | `true` | Inject context at session start |
| `recallPrompt` | *(default)* | Instruction appended to recalled context |
| `debug` | `false` | Verbose logs |

## How it works

### Memory search

When your agent calls `memory_search`, three sources are queried in parallel:

1. **MEMORY.md** — text search with surrounding context
2. **Knowledge Graph** — hybrid full-text + vector search across all indexed notes
3. **Active Tasks** — scans `memory/tasks/` for in-progress work

Results come back in clear sections so the agent knows where each piece of context came from.

### Auto-recall

On each session start, the plugin loads active tasks and recently modified notes, giving the agent immediate awareness of ongoing work without being asked.

### Auto-capture

After each conversation turn, the plugin records the exchange as a timestamped entry in a daily note. This builds a searchable history of everything your agent has discussed.

### Persistent connection

The plugin keeps a long-lived Basic Memory process running over standard I/O. No cold starts per tool call. The connection auto-reconnects if it drops.

## Agent tools

All tools accept an optional `project` parameter for cross-project operations.

| Tool | Description |
|------|-------------|
| `memory_search` | Composited search across all memory sources |
| `memory_get` | Read a specific note by title or path |
| `search_notes` | Search the knowledge graph directly |
| `read_note` | Read a note by title, permalink, or `memory://` URL |
| `write_note` | Create or update a note |
| `edit_note` | Append, prepend, find/replace, or replace a section |
| `delete_note` | Delete a note |
| `move_note` | Move a note to a different folder |
| `build_context` | Navigate the knowledge graph — follow relations and connections |
| `list_memory_projects` | List accessible projects |
| `list_workspaces` | List workspaces (personal and org) |
| `schema_validate` | Validate notes against Picoschema definitions |
| `schema_infer` | Analyze notes and suggest a schema |
| `schema_diff` | Detect drift between schema and actual usage |

## Slash commands

| Command | Description |
|---------|-------------|
| `/bm-setup` | Install or update the Basic Memory CLI |
| `/remember <text>` | Save a quick note |
| `/recall <query>` | Search the knowledge graph |
| `/tasks [args]` | Create, track, resume structured tasks |
| `/reflect [args]` | Consolidate recent notes into long-term memory |
| `/defrag [args]` | Reorganize and clean up memory files |
| `/schema [args]` | Manage Picoschema definitions |

## CLI

```bash
openclaw basic-memory search "auth patterns" --limit 5
openclaw basic-memory read "projects/api-redesign"
openclaw basic-memory context "memory://projects/api-redesign" --depth 2
openclaw basic-memory recent --timeframe 24h
openclaw basic-memory status
```

## Bundled skills

Ten skills ship with the plugin — no installation needed:

- **memory-defrag** — cleanup and reorganization of memory files
- **memory-ingest** — import existing material into Basic Memory
- **memory-lifecycle** — manage note/project lifecycle workflows
- **memory-literary-analysis** — analyze texts and reading notes
- **memory-metadata-search** — query notes by frontmatter fields
- **memory-notes** — guidance for writing well-structured notes
- **memory-reflect** — periodic consolidation of recent notes into durable memory
- **memory-research** — research synthesis into durable notes
- **memory-schema** — schema lifecycle (infer, create, validate, diff)
- **memory-tasks** — structured task tracking that survives context compaction

### Updating skills

```bash
bun run fetch-skills
```

In the monorepo, this copies from the canonical top-level [`skills/`](../../skills) directory into the generated `integrations/openclaw/skills/` package bundle.

## Task notes

The plugin works well with structured task notes in `memory/tasks/`:

```markdown
---
title: auth-middleware-rollout
type: Task
status: active
current_step: 2
---

## Context
Rolling JWT middleware to all API routes.

## Plan
- [x] Implement middleware
- [x] Add refresh-token validation
- [ ] Roll out to staging
- [ ] Verify logs and error rates
```

Set `status: done` to mark complete. Done tasks are filtered out of active task results.

## Basic Memory Cloud

Everything works locally. Cloud adds cross-device sync, team workspaces, and persistent memory for hosted agents.

- Same knowledge graph on laptop, desktop, and CI
- Shared workspaces for teams
- Durable memory for production agents

Cloud extends local-first — still plain Markdown, still yours. [Start a free trial](https://basicmemory.com) and use code `BMCLAW` for 20% off for 3 months. See [BASIC_MEMORY.md](./BASIC_MEMORY.md) for setup.

## Troubleshooting

**`bm` not found** — Install uv, then restart the gateway. Or install manually: `uv tool install basic-memory`

**Search returns nothing** — Check that Basic Memory connected (look for `connected to BM` in logs). Verify files exist in the project directory.

**Jiti cache issues** — `rm -rf /tmp/jiti/ "$TMPDIR/jiti/"` then restart the gateway.

**Disable semantic search** — Set `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=false` to fall back to full-text only.

## More

- [Memory + Task Flow](./MEMORY_TASK_FLOW.md) — practical runbook
- [Cloud Setup](./BASIC_MEMORY.md) — configure Basic Memory Cloud
- [Security](./SECURITY.md) — how auto-installation and data handling work
- [Development](./DEVELOPMENT.md) — contributing, tests, publishing
- [Basic Memory docs](https://docs.basicmemory.com)
- [Issues](https://github.com/basicmachines-co/basic-memory/issues)

## Telemetry

This plugin does not collect telemetry. The Basic Memory CLI may send anonymous usage analytics — see the [Basic Memory docs](https://github.com/basicmachines-co/basic-memory) for opt-out instructions.

## License

MIT
