---
name: memory-lifecycle
description: "Manage entity status transitions in Basic Memory: archive completed work, move notes between status folders, update frontmatter, and handle edge cases. Use when marking items complete, archiving old entities, or managing any folder-based status workflow."
---

# Memory Lifecycle

Manage how entities move through status stages in Basic Memory. The core principle: **archive, never delete.** Completed work is valuable context — move it out of the active view, but keep it in the knowledge graph.

## When to Use

- User says something is "done", "finished", "completed", "submitted", "missed", or "cancelled"
- Moving entities between status folders (active → archive, pipeline → active, etc.)
- Reverting a mistaken completion
- Periodic cleanup of stale active items

## Core Principle: Archive, Never Delete

Deleting a note removes it from the knowledge graph — all its observations, relations, and history disappear. Archiving preserves everything while signaling the entity is no longer active.

```
# Good — entity stays in the knowledge graph
move_note → active/ to archive/

# Bad — knowledge is lost
delete_note
```

The only exception: notes created by mistake (typos, true duplicates) can be deleted.

## Folder Conventions

Organize entities by status using folders. The exact folder names depend on your domain, but follow a consistent pattern:

```
entities/
  active/          # Currently relevant, in-progress
  archive/         # Completed, no longer active, but worth keeping
  pipeline/        # Future items, not yet started
```

For tasks specifically:

```
tasks/
  active/          # Work in progress
  completed/       # Finished work
```

For any entity type with a clear lifecycle:

```
[type]/
  active/          # Current
  [end-state]/     # Whatever "done" means for this type
```

Pick folder names that match your domain. The pattern matters more than the specific names.

## Status Detection

When the user mentions completion or status change, extract the intent:

| Signal | Status | Action |
|--------|--------|--------|
| "finished", "done", "completed", "shipped" | Complete | Move to archive/completed folder |
| "submitted", "sent", "delivered" | Complete | Move to archive/completed folder |
| "missed", "passed", "skipped", "expired" | Missed | Move to archive or missed folder |
| "cancelled", "abandoned", "killed" | Cancelled | Move to archive folder |
| "paused", "on hold", "deferred" | Paused | Update frontmatter status, keep in place |
| "restarting", "reopening", "reviving" | Reactivate | Move back to active folder |

## Workflow

### 1. Find the Entity

Search Basic Memory with multiple variations to locate the entity:

```python
search_notes(query="quarterly report")
search_notes(query="Q1 report")
```

If multiple matches come back, present options and ask which one.

If no match is found, ask for clarification — don't guess.

### 2. Move the File

Use `move_note` to relocate the entity to the appropriate status folder:

```python
move_note(
  identifier="tasks/active/quarterly-report",
  destination_path="tasks/completed/quarterly-report.md"
)
```

The permalink stays the same, so all existing `[[wiki-links]]` and `memory://` URLs continue to resolve.

### 3. Update Frontmatter

After moving, update the status in frontmatter to match:

```python
edit_note(
  identifier="quarterly-report",
  operation="find_replace",
  find_text="status: active",
  content="status: completed"
)
```

If there's a completion date field, set it:

```python
edit_note(
  identifier="quarterly-report",
  operation="find_replace",
  find_text="completed:",
  content="completed: 2026-02-22"
)
```

### 4. Confirm

Report what was done concisely:

```
Marked complete: Quarterly Report
  Moved to: tasks/completed/quarterly-report.md
  Status: completed
```

## Edge Cases

### Already Archived

If the entity is already in an archive/completed folder, notify the user:

> "Quarterly Report is already in tasks/completed/. Want me to update anything on it?"

### Partial Completion

Sometimes only part of an entity is done. Don't move it — instead, update observations or status notes within the entity to reflect partial progress.

### Revert / Reactivate

If something was archived by mistake, move it back:

```python
move_note(
  identifier="tasks/completed/quarterly-report",
  destination_path="tasks/active/quarterly-report.md"
)

edit_note(
  identifier="quarterly-report",
  operation="find_replace",
  find_text="status: completed",
  content="status: active"
)
```

### Status Without Movement

Some status changes don't require a folder move — "paused" or "blocked" items often stay in `active/` with just a frontmatter update. Reserve folder moves for terminal or major state transitions.

## Relationship to Other Skills

- **memory-tasks**: Tasks are a specific lifecycle case. This skill covers the general pattern; memory-tasks covers task-specific fields (steps, current_step, context).
- **memory-notes**: Use search-before-create (from memory-notes) to find the entity before transitioning it.
- **memory-defrag**: Periodic defrag can identify stale active items that should be archived.

## Guidelines

- **Archive, never delete.** The knowledge graph benefits from historical context.
- **Move first, then update frontmatter.** This order ensures the file is in the right place even if the edit step fails.
- **Permalinks survive moves.** Links to the entity keep working after a `move_note`.
- **Be concise in confirmations.** The user knows their system — just report what changed.
- **Ask when ambiguous.** If multiple entities match or the target folder isn't clear, ask rather than guess.
- **Batch operations are fine.** If the user says "archive all completed tasks", find them all, confirm the list, then move them in sequence.
