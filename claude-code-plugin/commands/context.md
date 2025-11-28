---
description: Build context from a Basic Memory URL
argument-hint: <memory://url> [depth] [timeframe]
allowed-tools: mcp__basic-memory__build_context, mcp__basic-memory__read_note
---

# Context

Build context from a Basic Memory memory:// URL.

## Arguments

- `$1` - Memory URL (e.g., `memory://topic`, `memory://folder/*`, `memory://SPEC-24`)
- `$2` - Depth of relation traversal (optional, default: 2)
- `$3` - Timeframe for recent changes (optional, default: "7d")

## Your Task

Navigate the knowledge graph and build comprehensive context.

1. **Build context** using `mcp__basic-memory__build_context`:
   - url: "$1"
   - depth: $2 or 2
   - timeframe: "$3" or "7d"

2. **Present the context**:
   - Main note content
   - Related notes found via relations
   - Recent changes within timeframe
   - Key observations and decisions

3. **Read additional notes** if needed for more detail.

## Memory URL Formats

- `memory://note-title` - Single note by title
- `memory://folder/*` - All notes in a folder
- `memory://SPEC-*` - Pattern matching
- `memory://specs/SPEC-24` - Note in specific project folder
