---
name: memory-continue
description: "Resume prior work by rebuilding context from the Basic Memory knowledge graph — pick up where you left off using memory:// URLs, recent activity, and search. Use when starting a session or when the user says 'continue with...', 'back to...', or 'where were we?'"
---

# Memory Continue

Resume previous work by reconstructing context from the Basic Memory knowledge graph, so the assistant can pick up across sessions instead of starting cold.

## When to Use

- Starting a new session and you need to pick up where you left off
- The user references earlier work: "continue with...", "back to...", "where were we on...?"
- You need context about an ongoing project or spec
- The user asks about something discussed in a previous conversation
- You're working on a task that spans multiple sessions

## Building Context

### 1. Identify What to Continue

If it's unclear, ask:
- What topic or project should you resume?
- What timeframe matters?
- Any specific aspect to focus on?

### 2. Gather Context with MCP Tools

**Known topic — use `build_context`.** Navigate the graph from a starting point, following relations outward:

```python
build_context(
    url="memory://topic-or-note-name",
    depth=2,           # how many relation hops to follow
    timeframe="7d",    # bias toward recent changes
)
```

**No clear starting point — use `recent_activity`.** See what's changed and let it surface the thread:

```python
recent_activity(timeframe="3d", depth=1)
```

**Looking for something specific — use `search_notes`.** Find candidate notes by keyword:

```python
search_notes(query="async client refactor", page_size=10)
```

### 3. Read the Key Notes

Once you've identified the relevant notes, read them in full:

```python
read_note(identifier="note-title-or-permalink")
```

### 4. Present Context to the User

Summarize what you found, incrementally:
- Current state of the work
- Recent changes or progress
- Open items and next steps
- Related context that might help

## Memory URL Reference

`build_context` and `read_note` both accept `memory://` URLs, which address notes by permalink and support wildcards for gathering groups of notes.

```
memory://note-title            # a single note by permalink
memory://folder/*              # all notes in a folder
memory://specs/SPEC-24*        # pattern / prefix match
memory://project/*/requirements # path wildcards
```

Use a specific note URL to anchor on one starting point; use a wildcard to pull in a whole folder or family of related notes at once.

## Timeframe Reference

`build_context` and `recent_activity` accept natural-language timeframes:

| Timeframe | Meaning |
|-----------|---------|
| `"today"` | Current day |
| `"yesterday"` | Previous day |
| `"3d"` or `"3 days"` | Last 3 days |
| `"1 week"` or `"7d"` | Last week |
| `"2 weeks"` | Last 2 weeks |
| `"1 month"` | Last month |

## Scenario Playbooks

### Resuming a Spec or Project

```python
# 1. Read the spec / project note
read_note(identifier="SPEC-24: Postgres Database Migration")

# 2. Pull in related context and recent changes via the graph
build_context(url="memory://SPEC-24*", timeframe="7d")
```

Then summarize: the goals, what's completed, what's pending, and any blockers or open decisions.

### Continuing General Work

```python
# 1. Check recent activity
recent_activity(timeframe="3d")

# 2. Read notes from the recent sessions it surfaces
read_note(identifier="relevant-note")
```

Then list the modified notes with brief descriptions and ask which thread to dive into.

### Following Up on a Topic

```python
# 1. Find the topic
search_notes(query="topic keywords")

# 2. Build context from the best match, following its relations
build_context(url="memory://found-note-permalink", depth=2)
```

Then present the full picture — the note plus its connected context.

## Project Discovery

Project names are user-specific. To discover what's available before scoping a search or `memory://` URL:

```python
list_memory_projects()
```

In multi-project setups, prefix a `memory://` URL with the project name (e.g. `memory://research/papers/crdt`) to scope it.

## Guidelines

1. **Start broad, then narrow.** Get an overview with `recent_activity` or a wildcard `build_context`, then drill into specific notes.
2. **Present incrementally.** Share what you find as you go rather than holding everything until the end.
3. **Follow relations.** The graph's connections are the point — `build_context` with `depth` surfaces context you wouldn't find by reading one note.
4. **Check multiple projects.** Specs may live separately from implementation notes; discover projects with `list_memory_projects`.
5. **Confirm understanding.** Verify the reconstructed context is what the user actually needs before acting on it.
6. **Capture new progress.** As the resumed work advances, write it back to the graph (see the **memory-notes** skill) so the next session can continue too.
