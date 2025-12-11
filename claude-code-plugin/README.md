# Basic Memory Plugin for Claude Code

A Claude Code plugin that integrates [Basic Memory](https://basicmemory.io) - a local-first knowledge management system built on the Model Context Protocol (MCP).

## What This Plugin Does

This plugin helps Claude Code work seamlessly with your Basic Memory knowledge base:

- **Capture knowledge** from conversations automatically
- **Resume previous work** by building context from your knowledge graph
- **Edit notes** interactively through conversation
- **Organize your knowledge** by finding orphans, suggesting links, and maintaining structure

## Installation

### 1. Install Basic Memory

```bash
pip install basic-memory
# or
pipx install basic-memory
```

### 2. Add the Marketplace

```
/plugin marketplace add basicmachines-co/basic-memory/claude-code-plugin
```

### 3. Install the Plugin

```
/plugin install basic-memory@basicmachines
```

## Commands

| Command | Description |
|---------|-------------|
| `/remember [title]` | Capture insights from the current conversation |
| `/continue [topic]` | Resume previous work with context |
| `/context <memory://url>` | Build context from a specific note |
| `/recent [timeframe]` | Show recent activity |
| `/organize [action]` | Maintain your knowledge graph |
| `/research <topic>` | Research a topic and save a report |

### Examples

```bash
# Capture what we just discussed
/remember "Database Design Decision"

# Pick up where we left off
/continue postgres migration

# Check recent changes
/recent 1week

# Find orphan notes and suggest links
/organize orphans

# Research a topic and save findings
/research "MCP protocol"
```

## Skills

Skills are model-invoked - Claude uses them automatically when the context fits.

| Skill | What It Does |
|-------|--------------|
| `knowledge-capture` | Auto-captures decisions and insights into structured notes |
| `continue-conversation` | Builds context when resuming previous work |
| `spec-driven-development` | Guides implementation based on specs in Basic Memory |
| `edit-note` | Edits notes via MCP tools (cloud-compatible) |
| `edit-note-local` | Edits notes as files (local installations) |
| `knowledge-organize` | Helps organize and link notes |
| `research` | Researches topics and produces saved reports |

## Hooks

| Event | Behavior |
|-------|----------|
| `PostToolUse: write_note` | Confirms when notes are saved |
| `Stop` | Suggests capturing valuable insights after conversations |

## Requirements

- [Claude Code](https://claude.com/claude-code)
- [Basic Memory](https://basicmemory.io) with MCP server configured

## Documentation

- [Full Plugin Documentation](./PLUGIN.md)
- [Basic Memory Docs](https://docs.basicmemory.io)
- [Claude Code Plugins](https://code.claude.com/docs/en/plugins)

## License

MIT - See the [Basic Memory repository](https://github.com/basicmachines-co/basic-memory) for details.
