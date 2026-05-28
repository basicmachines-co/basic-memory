# Basic Memory CLI & Cloud Setup

## How the Plugin Connects

Basic Memory is **local-first** — everything works out of the box with no cloud account, no internet connection, and no external services. Your notes live as markdown files on disk, indexed locally with SQLite.

The plugin spawns a Basic Memory MCP session via stdio:

```
bm mcp --transport stdio --project <name>
```

All tool calls route through this MCP session. No cloud configuration is required for normal use.

## Cloud Configuration (Optional)

Cloud sync is entirely optional. If you want to sync your local knowledge base to Basic Memory Cloud for backup or cross-device access, you can configure per-project cloud routing.

### Why Cloud?

- **Your agent's memory travels with you** — laptop, desktop, hosted environment. Same knowledge graph everywhere, synced bidirectionally.
- **Team knowledge sharing** — org workspaces let multiple agents and team members build on a shared knowledge base.
- **Durable memory for production agents** — CI runners, containers, and hosted environments are ephemeral. Cloud gives agents persistent memory that survives teardowns.
- **Multi-agent coordination** — multiple agents (or the same agent across services) can read and write to a shared graph.

Cloud extends local-first — it doesn't replace it. Your notes are still plain markdown, still editable locally, still yours. Start with a [7-day free trial](https://basicmemory.com) — no credit card required. Use code `BMCLAW` for 20% off for 3 months.

### Setup

```bash
# Authenticate with Basic Memory Cloud
bm cloud login

# Save API key for per-project cloud routing
bm cloud set-key bmc_...

# Route a project through the cloud
bm project set-cloud <name>

# Revert a project to local routing
bm project set-local <name>

# Check cloud connection state
bm cloud status
```

When a project is set to cloud mode, the MCP server routes tool calls for that project through the cloud API using the saved API key as a Bearer token. Local projects (the default) continue to use the local SQLite index. You can mix local and cloud projects freely.

## Project Management

```bash
# List all projects
bm project list

# Add a new project
bm project add "name" ~/path

# Show current project details
bm project info

# Set the default project
bm project default "name"

# One-way sync (local -> cloud)
bm project sync

# Bidirectional sync
bm project bisync
```

## Cross-Project Operations

All plugin tools accept an optional `project` parameter to operate on a different project:

```
search_notes(query="authentication", project="other-project")
read_note(identifier="notes/api-design", project="docs")
write_note(title="New Note", content="...", folder="notes", project="research")
```

## Workspace Support

Workspaces group projects by owner (personal or organization):

```
list_workspaces()

list_memory_projects(workspace="my-org")
```

## Auto-Recall

When `autoRecall` is enabled (the default), the plugin injects relevant context at the start of each agent session by listening for the `agent_start` event. On each trigger it:

1. **Queries active tasks** — searches the knowledge graph for notes with `type: Task` and `status: active` (up to 5 results)
2. **Fetches recent activity** — gets notes modified in the last 24 hours
3. **Formats and injects context** — returns the results as structured context for the agent

The injected context looks like:

```
## Active Tasks
- **Fix login bug** — Description of Fix login bug
- **Update API docs** — Description of Update API docs

## Recent Activity
- Daily standup notes (memory/daily-standup-notes.md)
- API design decisions (memory/api-design-decisions.md)

---
Check for active tasks and recent activity. Summarize anything relevant to the current session.
```

The trailing instruction (after `---`) is the `recallPrompt`, which you can customize to change what the agent focuses on. For example:

```json
{
  "recallPrompt": "Focus on blocked tasks and any decisions made in the last 24 hours."
}
```

To disable auto-recall entirely:

```json
{
  "autoRecall": false
}
```

## Auto-Capture

When `autoCapture` is enabled (the default), the plugin automatically records agent conversations after each turn:

1. Extracts the last user + assistant messages
2. Appends them as timestamped entries to a daily conversation note (`conversations-YYYY-MM-DD`)
3. Skips very short exchanges (< `captureMinChars` chars each, default 10)

This builds a searchable history of agent interactions in the knowledge graph without any manual effort.

## Slash Commands

### Memory commands

- **`/remember <text>`** — Save a quick note to the knowledge graph
- **`/recall <query>`** — Search the knowledge graph (top 5 results)

### Skill workflows

These commands inject step-by-step workflow instructions from the bundled skill files:

| Command | What it does |
|---------|-------------|
| `/tasks` | Task management — create, track, resume structured tasks that survive context compaction |
| `/reflect` | Memory reflection — review recent activity and consolidate insights into long-term memory |
| `/defrag` | Memory defrag — reorganize, split, prune, and clean up memory files |
| `/schema` | Schema management — infer, create, validate, and evolve Picoschema definitions |

Each command accepts optional arguments for context:

```
/tasks create a task for the API migration
/reflect focus on decisions from this week
/defrag clean up completed tasks older than 2 weeks
/schema infer a schema for Meeting notes
```

When invoked without arguments, the agent receives the full workflow instructions and follows them interactively.

## Plugin Configuration

The plugin accepts these config fields in `openclaw.config.json`:

```json
{
  "plugins": {
    "entries": {
      "openclaw-basic-memory": {
        "enabled": true,
        "config": {
          "project": "my-project",
          "memoryDir": "memory/",
          "memoryFile": "MEMORY.md",
          "autoCapture": true,
          "autoRecall": true,
          "recallPrompt": "Check for active tasks and recent activity. Summarize anything relevant.",
          "debug": false
        }
      }
    },
    "slots": {
      "memory": "openclaw-basic-memory"
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `project` | `openclaw-<hostname>` | BM project name |
| `bmPath` | `bm` | Path to BM CLI binary |
| `memoryDir` | `memory/` | Relative path for memory files |
| `memoryFile` | `MEMORY.md` | Working memory file name |
| `projectPath` | same as `memoryDir` | Absolute path to project root |
| `autoCapture` | `true` | Auto-index conversations after each turn |
| `captureMinChars` | `10` | Min chars to trigger capture |
| `autoRecall` | `true` | Inject context (active tasks, recent activity) at session start |
| `recallPrompt` | *(see above)* | Instruction appended to recalled context |
| `debug` | `false` | Verbose logging |
