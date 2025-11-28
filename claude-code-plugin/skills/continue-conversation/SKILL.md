---
name: continue-conversation
description: Resume previous work by building context from Basic Memory knowledge graph using memory URLs and recent activity
---

# Continue Conversation

This skill helps you resume previous work by building context from the Basic Memory knowledge graph, enabling seamless continuation across sessions.

## When to Use

Use this skill when:
- Starting a new session and need to pick up where you left off
- User mentions previous work ("continue with...", "back to...", "where were we with...")
- Need context about ongoing projects or specs
- User asks about something discussed in a previous conversation
- Working on a multi-session task

## Building Context

### 1. Identify What to Continue

Ask if unclear:
- What topic or project to resume?
- What timeframe to look at?
- Any specific aspect to focus on?

### 2. Gather Context with MCP Tools

**Option A: Known Topic - Use build_context**

```python
# Navigate knowledge graph from a known starting point
mcp__basic-memory__build_context(
    url="memory://topic-or-note-name",
    depth=2,           # How many relation hops to follow
    timeframe="7d",    # Recent changes
    project="main"     # or "specs" for specifications
)
```

Memory URL formats:
- `memory://note-title` - Single note
- `memory://folder/*` - All notes in folder
- `memory://specs/SPEC-24*` - Pattern matching

**Option B: Recent Activity - What's been happening?**

```python
# See what's changed recently
mcp__basic-memory__recent_activity(
    timeframe="3d",    # "1d", "1 week", "2 weeks"
    depth=1,
    project="main"
)
```

**Option C: Search for Context**

```python
# Find relevant notes
mcp__basic-memory__search_notes(
    query="search terms",
    page_size=10,
    project="main"
)
```

### 3. Read Key Notes

Once you identify relevant notes:

```python
mcp__basic-memory__read_note(
    identifier="note-title-or-permalink",
    project="main"
)
```

### 4. Present Context to User

Summarize what you found:
- Current state of the work
- Recent changes or progress
- Open items or next steps
- Related context that might be helpful

## Context Strategies by Scenario

### Resuming a Spec Implementation

```python
# 1. Read the spec
mcp__basic-memory__read_note(
    identifier="SPEC-24: Postgres Database Migration",
    project="specs"
)

# 2. Check recent activity on related topics
mcp__basic-memory__build_context(
    url="memory://SPEC-24*",
    timeframe="7d",
    project="specs"
)

# 3. Look at what's been done in the codebase
# (Use regular file tools for this)
```

### Continuing General Work

```python
# 1. Check recent activity across projects
mcp__basic-memory__recent_activity(
    timeframe="3d",
    project="main"
)

# 2. Read any notes from recent sessions
mcp__basic-memory__read_note(
    identifier="relevant-note",
    project="main"
)
```

### Following Up on a Topic

```python
# 1. Search for the topic
mcp__basic-memory__search_notes(
    query="topic keywords",
    project="main"
)

# 2. Build context from best match
mcp__basic-memory__build_context(
    url="memory://found-note-permalink",
    depth=2,
    project="main"
)
```

## Timeframe Reference

Natural language timeframes:
- `"today"` - Current day
- `"yesterday"` - Previous day
- `"3d"` or `"3 days"` - Last 3 days
- `"1 week"` or `"7d"` - Last week
- `"2 weeks"` - Last 2 weeks
- `"1 month"` - Last month

## Project Reference

Common projects:
- `main` - Primary knowledge base
- `specs` - Specifications and design docs
- `basic-memory-llc` - Business/company notes
- `getting-started` - Tutorial content

List available projects:
```python
mcp__basic-memory__list_memory_projects()
```

## Example Conversations

### User: "Let's continue with the Postgres migration"

```
1. Read SPEC-24 from specs project
2. Check for related notes about implementation progress
3. Summarize:
   - Spec overview and goals
   - What's been completed (checkmarks)
   - What's pending (checkboxes)
   - Any blockers or decisions needed
```

### User: "What was I working on yesterday?"

```
1. Get recent activity for last 2 days
2. List modified notes with brief descriptions
3. Ask which topic to dive into
```

### User: "Back to the async client pattern"

```
1. Search for "async client pattern"
2. Build context from matching note
3. Include related notes via relations
4. Present the full picture
```

## Best Practices

1. **Start broad, then narrow** - Get overview first, then specific details
2. **Follow relations** - Knowledge graph connections are valuable
3. **Check multiple projects** - Specs might be separate from implementation notes
4. **Present incrementally** - Share what you find as you go
5. **Confirm understanding** - Verify the context is what user needs
6. **Update as you go** - Capture new progress in notes during the session

## Combining with Other Skills

After building context, you might:
- Use **knowledge-capture** to document new progress
- Use **spec-driven-development** if continuing a spec implementation
- Create new notes linking to the context you gathered
