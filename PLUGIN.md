# Basic Memory Plugin for Claude Code

This plugin provides Claude Code skills for working with [Basic Memory](https://basicmemory.io) - a local-first knowledge management system built on the Model Context Protocol (MCP).

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

### Via Plugin Command

```
/plugin install basic-memory@basicmachines
```

### Via Repository Settings

Add to your `.claude/settings.json`:

```json
{
  "plugins": {
    "marketplaces": ["basicmachines"],
    "installed": ["basic-memory@basicmachines"]
  }
}
```

## Skills Included

### knowledge-capture

Automatically capture insights, decisions, and learnings from conversations into structured Basic Memory notes.

**Triggers when:**
- Important decisions are made
- Technical insights are discovered
- Problems are solved
- Design trade-offs are discussed

**What it does:**
- Creates structured notes with observations and relations
- Uses appropriate categories (`[decision]`, `[insight]`, `[pattern]`, etc.)
- Links to related knowledge in the graph

### continue-conversation

Resume previous work by building context from the Basic Memory knowledge graph.

**Triggers when:**
- Starting a new session
- User mentions previous work ("continue with...", "back to...")
- Need context about ongoing projects

**What it does:**
- Uses `build_context` with memory:// URLs
- Checks `recent_activity` to see what's changed
- Presents relevant context for seamless continuation

### spec-driven-development

Guide implementation based on specifications stored in Basic Memory.

**Triggers when:**
- Implementing a feature defined by a spec
- Creating new specifications
- Reviewing implementation against criteria

**What it does:**
- Follows the SPEC-1 process (Create → Discuss → Implement → Validate → Document)
- Updates spec progress as work completes
- Maintains living documentation with checklists

## How Skills Work

Unlike slash commands (user-invoked), skills are **model-invoked** - Claude automatically decides when to use them based on conversation context. You don't need to explicitly call them.

## MCP Tools Used

These skills leverage Basic Memory's MCP tools:

- `write_note` - Create/update markdown notes
- `read_note` - Read notes by title or permalink
- `search_notes` - Full-text search across content
- `build_context` - Navigate knowledge graph via memory:// URLs
- `recent_activity` - Get recently updated information
- `edit_note` - Incrementally update notes

## Related

- [Basic Memory Documentation](https://docs.basicmemory.io)
- [Basic Memory GitHub](https://github.com/basicmachines-co/basic-memory)
- [Model Context Protocol](https://modelcontextprotocol.io)
