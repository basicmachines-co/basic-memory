---
description: Resume previous work from Basic Memory context
argument-hint: [topic]
allowed-tools: mcp__basic-memory__build_context, mcp__basic-memory__recent_activity, mcp__basic-memory__search_notes, mcp__basic-memory__read_note
---

# Continue

Resume previous work by building context from Basic Memory.

## Arguments

- `$ARGUMENTS` - Topic, note title, or search terms to find previous context

## Your Task

Build context to continue previous work seamlessly.

1. **Find relevant context**:

   If a specific topic is provided ("$ARGUMENTS"):
   - Search for matching notes: `mcp__basic-memory__search_notes`
   - Build context from matches: `mcp__basic-memory__build_context`
   - Read key notes for details: `mcp__basic-memory__read_note`

   If no topic provided:
   - Get recent activity: `mcp__basic-memory__recent_activity` with timeframe "3d"
   - Present what's been happening
   - Ask which topic to dive into

2. **Present context**:
   - Summarize current state of the work
   - Highlight recent changes or progress
   - List open items or next steps
   - Show related context from the knowledge graph

3. **Be ready to continue**:
   - Understand what was done before
   - Know what needs to happen next
   - Have relevant context loaded

## Tips

- Use `memory://topic` URL format with `build_context`
- Check multiple projects if needed (main, specs)
- Follow relations to find connected knowledge
