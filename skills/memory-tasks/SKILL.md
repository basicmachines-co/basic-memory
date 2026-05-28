---
name: memory-tasks
description: "Task management via Basic Memory schemas: create, track, and resume structured tasks that survive context compaction. Uses BM's schema system for uniform notes queryable through the knowledge graph."
---

# Memory Tasks

Manage work-in-progress using Basic Memory's schema system. Tasks are just notes with `type: Task` — they live in the knowledge graph, validate against a schema, and survive context compaction.

## When to Use

- **Starting multi-step work** (3+ steps, or anything that might outlast the context window)
- **After compaction/restart** — search for active tasks to resume
- **Pre-compaction flush** — update all active tasks with current state
- **On demand** — user asks to create, check, or manage tasks

## Task Schema

Tasks use the BM schema system (SPEC-SCHEMA). The schema note lives at `memory/schema/Task.md`:

```yaml
---
title: Task
type: schema
entity: Task
version: 1
schema:
  description: string, what needs to be done
  status?(enum): [active, blocked, done, abandoned], current state
  assigned_to?: string, who is working on this
  steps?(array): string, ordered steps to complete
  current_step?: integer, which step number we're on (1-indexed)
  context?: string, key context needed to resume after memory loss
  started?: string, when work began
  completed?: string, when work finished
  blockers?(array): string, what's preventing progress
  parent_task?: Task, parent task if this is a subtask
settings:
  validation: warn
---
```

## Creating a Task

When work qualifies, create a task note. Use `write_note` with `note_type="Task"` and put queryable fields in `metadata`:

```python
write_note(
  title="Descriptive task name",
  directory="tasks",
  note_type="Task",
  metadata={
    "status": "active",
    "priority": "high",
    "current_step": 1,
    "steps": ["First step", "Second step", "Third step"]
  },
  tags=["task"],
  content="""# Descriptive task name

## Observations
- [description] What needs to be done, concisely
- [status] active
- [assigned_to] claude
- [current_step] 1

## Steps
1. [ ] First concrete step
2. [ ] Second concrete step
3. [ ] Third concrete step

## Context
What future-you needs to pick up this work. Include:
- Key file paths and repos involved
- Decisions already made and why
- What was tried and what worked/didn't
- Where to look for related context"""
)
```

**Why both frontmatter and observations?** Fields in `metadata` (stored as frontmatter) power `search_notes` with `metadata_filters`. Fields as observations (`- [status] active`) power `schema_validate`. Include queryable fields in both places for full coverage.

### Key Principles

- **Steps are concrete and checkable** — "Implement X in file Y", not "figure out stuff"
- **Context is for post-amnesia resumption** — Write it as if explaining to a smart person who knows nothing about what you've been doing
- **Relations link to other entities** — `parent_task [[Other Task]]`, `related_to [[Some Note]]`
- **`note_types` is case-sensitive** — `write_note(note_type="Task")` stores the type as lowercase `task` in frontmatter. Use `note_types=["task"]` (lowercase) in search queries.

## Resuming After Compaction

On session start or after compaction:

1. **Search for active tasks:**
   ```python
   search_notes(note_types=["task"], status="active")
   ```

2. **Read the task note** to get full context

3. **Resume from `current_step`** using the `context` field

4. **Update as you progress** — increment `current_step`, update context, check off steps

## Updating Tasks

As work progresses, update the task note:

```markdown
## Steps
1. [x] First step — done, resulted in X
2. [x] Second step — done, changed approach because Y
3. [ ] Third step — next up

## Context
Updated context reflecting current state...
```

Update frontmatter too:
```yaml
current_step: 3
```

## Completing Tasks

When done:
```yaml
status: done
completed: YYYY-MM-DD
```

Add a brief summary of what was accomplished and any follow-up needed.

## Pre-Compaction Flush

When a compaction event is imminent:

1. Find all active tasks: `search_notes(note_types=["task"], status="active")`
2. For each, update:
   - `current_step` to reflect actual progress
   - `context` with everything needed to resume
   - Step checkboxes to show what's done
3. This is **critical** — context not written down is context lost

## Querying Tasks

With BM's schema system, tasks are fully queryable:

| Query | What it finds |
|-------|--------------|
| `search_notes(note_types=["task"])` | All tasks |
| `search_notes(note_types=["task"], status="active")` | Active tasks |
| `search_notes(note_types=["task"], status="blocked")` | Blocked tasks |
| `search_notes(note_types=["task"], metadata_filters={"assigned_to": "claude"})` | My tasks |
| `search_notes("blockers", note_types=["task"])` | Tasks with blockers |
| `schema_validate(noteType="Task")` | Validate all tasks against schema |
| `schema_diff(noteType="Task")` | Detect drift between schema and actual task notes |

## Guidelines

- **One task per unit of work** — Don't cram multiple projects into one task
- **Externalize early** — If you think "I should remember this", write it down NOW
- **Context > steps** — Steps tell you what to do; context tells you why and how
- **Close finished tasks** — Don't leave completed work as `active`
- **Link related tasks** — Use `parent_task [[X]]` or relations to connect related work
- **Schema validation is your friend** — Run `schema_validate(noteType="Task")` periodically to catch incomplete tasks
