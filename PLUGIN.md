# Basic Memory Plugin for Claude Code

This plugin provides skills, commands, and hooks for working with [Basic Memory](https://basicmemory.io) - a local-first knowledge management system built on the Model Context Protocol (MCP).

## Prerequisites

You need the Basic Memory MCP server running. Install it via:

```bash
# Install basic-memory
pip install basic-memory

# Or with pipx
pipx install basic-memory
```

Then add it to your Claude Code MCP configuration.

## Installation

### Add the Marketplace

```
/plugin marketplace add basicmachines-co/basic-memory
```

### Install the Plugin

```
/plugin install basic-memory@basicmachines
```

### Or via Repository Settings

Add to your `.claude/settings.json`:

```json
{
  "plugins": {
    "extraKnownMarketplaces": {
      "basicmachines": {
        "source": {
          "source": "github",
          "repo": "basicmachines-co/basic-memory"
        }
      }
    },
    "installed": ["basic-memory@basicmachines"]
  }
}
```

---

## Slash Commands

User-invoked commands for explicit interaction with Basic Memory.

### `/remember [title] [folder]`

Capture insights, decisions, or learnings from the current conversation.

```
/remember "FastAPI Async Pattern"
/remember "Auth Decision" decisions
```

Creates a structured note with:
- Context from the conversation
- Observations with `[decision]`, `[insight]`, `[pattern]` categories
- Relations linking to related concepts

### `/continue [topic]`

Resume previous work by building context from Basic Memory.

```
/continue postgres migration
/continue SPEC-24
/continue
```

If no topic is provided, shows recent activity and asks what to dive into.

### `/context <memory://url> [depth] [timeframe]`

Build context from a specific memory:// URL.

```
/context memory://SPEC-24
/context memory://architecture/* 3 2weeks
```

### `/recent [timeframe] [project]`

Show recent activity in Basic Memory.

```
/recent
/recent 1week
/recent today specs
```

---

## Skills

Model-invoked capabilities that Claude uses automatically based on context.

### knowledge-capture

Automatically captures insights, decisions, and learnings into structured notes.

**Triggers when:**
- Important decisions are made
- Technical insights are discovered
- Problems are solved
- Design trade-offs are discussed

### continue-conversation

Resumes previous work by building context from the knowledge graph.

**Triggers when:**
- Starting a new session
- User mentions previous work ("continue with...", "back to...")
- Need context about ongoing projects

### spec-driven-development

Guides implementation based on specifications stored in Basic Memory.

**Triggers when:**
- Implementing a feature defined by a spec
- Creating new specifications
- Reviewing implementation against criteria

---

## Hooks

Automated behaviors that enhance the Basic Memory workflow.

### PostToolUse: write_note

Confirms when notes are saved to Basic Memory.

### Stop

After significant conversations, suggests using `/remember` to capture valuable insights (only when genuinely useful).

---

## MCP Tools Used

This plugin leverages Basic Memory's MCP tools:

| Tool | Purpose |
|------|---------|
| `write_note` | Create/update markdown notes |
| `read_note` | Read notes by title or permalink |
| `search_notes` | Full-text search across content |
| `build_context` | Navigate knowledge graph via memory:// URLs |
| `recent_activity` | Get recently updated information |
| `edit_note` | Incrementally update notes |

---

## Plugin Structure

```
basic-memory/
├── .claude-plugin/
│   ├── plugin.json          # Plugin manifest
│   └── marketplace.json     # Self-hosted marketplace
├── commands/
│   ├── remember.md          # /remember command
│   ├── continue.md          # /continue command
│   ├── context.md           # /context command
│   └── recent.md            # /recent command
├── skills/
│   ├── knowledge-capture/
│   ├── continue-conversation/
│   └── spec-driven-development/
├── hooks/
│   └── hooks.json           # Hook definitions
└── PLUGIN.md                # This file
```

---

## Related

- [Basic Memory Documentation](https://docs.basicmemory.io)
- [Basic Memory GitHub](https://github.com/basicmachines-co/basic-memory)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [Claude Code Plugins](https://code.claude.com/docs/en/plugins)
