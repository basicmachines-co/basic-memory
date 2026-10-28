> Pi package reference only.
> These canonical Basic Memory instructions may mention direct MCP tools such as
> `bm_read`, `bm_write`, `bm_search`, or `bm_context`.
> In Pi CLI mode, use the active `basic-memory-pi` skill with `bm_recall` and
> `bm_capture` instead. In Pi MCP mode, only use direct Basic Memory tool names
> if the MCP adapter exposes them in the current runtime.

---
name: memory-continue
description: "Resume prior work by rebuilding context from the Basic Memory graph via memory:// URLs, recent activity, and search. Use at session start or on 'continue with...', 'back to...', 'where were we?'"
---

# Memory Continue

Resume previous work by reconstructing context from the Basic Memory knowledge graph, so the assistant can pick up across sessions instead of starting cold.

## Project scope

If the project is unknown, discover it with `bm_project(command="list")`.
Pass a nonempty `project` on every note, folder, context and schema call, even
with one project or a qualified `memory://` URL. Use the chosen project's name,
`workspace/project`, or external ID; `<project>` below is a placeholder for it.
Recent activity also requires `project`. Search accepts `project` for one
project, `projects` for selected projects, or `all_projects=True` explicitly.
Discovery and bundled manuals need no project. Keep the same scope when following
returned paths, paginating, or recovering from a refusal.

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
- What since matters?
- Any specific aspect to focus on?

### 2. Gather Context with MCP Tools

**Known topic — use `bm_context`.** Navigate the graph from a starting point, following relations outward:

```python
bm_context(
    identifier="memory://topic-or-note-name",
    depth=2,           # how many relation hops to follow
    since="7d",        # only include changes in this window
    project="<project>",
)
```

**No clear starting point — use `bm_project(command="recent", project="<project>")`.** See what's changed and let it surface the thread:

```python
bm_project(command="recent", since="3d", project="<project>")
```

**Looking for something specific — use `bm_search`.** Find candidate notes by keyword:

```python
bm_search(query="async client refactor", page_size=10, project="<project>")
```

### 3. Read the Key Notes

Once you've identified the relevant notes, read them in full:

```python
bm_read(identifier="note-title-or-permalink", project="<project>")
```

For long notes, pick before you pay for bodies. `compact=True` drops bodies from
search results (and observations from `bm_context`), and a line range or a
section reads only the part you need:

```python
bm_search(query="async client refactor", compact=True, project="<project>")
bm_read(identifier="note-title-or-permalink", start_line=120, end_line=180, project="<project>")
bm_read(identifier="note-title-or-permalink", section="Status", project="<project>")
```

### 4. Present Context to the User

Summarize what you found, incrementally:
- Current state of the work
- Recent changes or progress
- Open items and next steps
- Related context that might help

## Memory URL Reference

`bm_context` and `bm_read` both accept `memory://` URLs. `bm_read` resolves one exact note; use `bm_context` for wildcard patterns that gather groups of notes.

```
memory://note-title            # a single note by permalink
memory://folder/*              # all notes in a folder
memory://specs/SPEC-24*        # pattern / prefix match
memory://project/*/requirements # path wildcards
```

Use a specific note URL to anchor on one starting point; use a wildcard to pull in a whole folder or family of related notes at once.

## Timeframe Reference

`bm_context` and `bm_project(command="recent", project="<project>")` accept natural-language timeframes:

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
bm_read(identifier="SPEC-24: Postgres Database Migration", project="<project>")

# 2. Pull in related context and recent changes via the graph
bm_context(identifier="memory://SPEC-24*", since="7d", project="<project>")
```

Then summarize: the goals, what's completed, what's pending, and any blockers or open decisions.

### Continuing General Work

```python
# 1. Check recent activity
bm_project(command="recent", since="3d", project="<project>")

# 2. Read notes from the recent sessions it surfaces
bm_read(identifier="relevant-note", project="<project>")
```

Then list the modified notes with brief descriptions and ask which thread to dive into.

### Following Up on a Topic

```python
# 1. Find the topic
bm_search(query="topic keywords", project="<project>")

# 2. Build context from the best match, following its relations
bm_context(identifier="memory://found-note-permalink", depth=2, project="<project>")
```

Then present the full picture — the note plus its connected context.

## Project Discovery

Project names are user-specific. To discover what's available before scoping a search or `memory://` URL:

```python
bm_project(command="list")
```

A qualified `memory://` URL (e.g. `memory://research/papers/crdt`) must agree
with `project`; the prefix does not replace that required parameter.

For an explicitly broader search, use one of these scopes instead:

```python
bm_search(query="topic keywords", projects=["research", "team/engineering"])
bm_search(query="topic keywords", all_projects=True)
```

## Guidelines

1. **Start broad, then narrow.** Get an overview with `bm_project(command="recent", project="<project>")` or a wildcard `bm_context`, then drill into specific notes.
2. **Present incrementally.** Share what you find as you go rather than holding everything until the end.
3. **Follow relations.** The graph's connections are the point — `bm_context` with `depth` surfaces context you wouldn't find by reading one note.
4. **Check multiple projects.** Specs may live separately from implementation notes; discover projects with `bm_project(command="list")`.
5. **Confirm understanding.** Verify the reconstructed context is what the user actually needs before acting on it.
6. **Capture new progress.** As the resumed work advances, write it back to the graph (see the **memory-notes** skill) so the next session can continue too.
