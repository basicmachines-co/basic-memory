---
name: memory-lifecycle
description: "Manage note status transitions in Basic Memory: archive completed work, move notes between status folders, update frontmatter. Use when marking items complete or archiving entities."
---

# Memory Lifecycle

Manage how entities move through status stages in Basic Memory. The core principle: **archive, never delete.** Completed work is valuable context — move it out of the active view, but keep it in the knowledge graph.

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

- User says something is "done", "finished", "completed", "submitted", "missed", or "cancelled"
- Moving entities between status folders (active → archive, pipeline → active, etc.)
- Reverting a mistaken completion
- Periodic cleanup of stale active items

## Core Principle: Archive, Never Delete

Deleting a note removes it from the knowledge graph — its observations and relations disappear, and links to it from other notes become unresolved links. Archiving preserves everything while signaling the entity is no longer active.

```
# Good — entity stays in the knowledge graph
bm_move → active/ to archive/

# Bad — knowledge is lost
bm_delete
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
bm_search(query="quarterly report", project="<project>")
bm_search(query="Q1 report", project="<project>")
```

If multiple matches come back, present options and ask which one.

If no match is found, ask for clarification — don't guess.

### 2. Move the File

Use `bm_move` to relocate the entity to the appropriate status folder:

```python
bm_move(
  identifier="tasks/active/quarterly-report",
  destination="tasks/completed/quarterly-report.md",
  project="<project>",
)
```

By default the permalink stays the same after a move, so links keep resolving. Projects with `update_permalinks_on_move` enabled rewrite it from the new path.

### 3. Update Frontmatter

After moving, merge the new status (and completion date, if the type tracks one) into
frontmatter with `command="set_metadata"` and the `metadata` parameter; this leaves
the body unchanged:

```python
bm_edit(
  identifier="tasks/completed/quarterly-report.md",
  command="set_metadata",
  metadata={"status": "completed", "completed": "2026-02-22"},
  project="<project>",
)
```

If the note also carries a `- [status]` observation, update it too (for example with
`find_replace` on `- [status] active`).

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

### Locked Notes

A note with `locked: true` in its frontmatter still moves, and keeps its lock,
but the status edit in step 3 is refused. Report that it moved and that its
status is unchanged. Only the user can unlock it, by editing the file.

### Revert / Reactivate

If something was archived by mistake, move it back:

```python
bm_move(
  identifier="tasks/completed/quarterly-report",
  destination="tasks/active/quarterly-report.md",
  project="<project>",
)

bm_edit(
  identifier="tasks/active/quarterly-report.md",
  command="set_metadata",
  metadata={"status": "active"},
  project="<project>",
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
- **Permalinks survive moves by default.** Links to the entity keep working after a `bm_move` unless the project enables `update_permalinks_on_move`.
- **Be concise in confirmations.** The user knows their system — just report what changed.
- **Ask when ambiguous.** If multiple entities match or the target folder isn't clear, ask rather than guess.
- **Batch operations are fine.** If the user says "archive all completed tasks", find them all, confirm the list, then move them in sequence.
