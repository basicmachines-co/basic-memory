---
title: Task
type: schema
entity: Task
version: 1
schema:
  description: string, what needs to be done
  status?(enum, current state): [active, blocked, done, abandoned]
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

# Task

A **Task** is work-in-progress tracked as a note, so it survives context
compaction and shows up in the next session's brief. This schema is the same one
the framework-agnostic [`memory-tasks`](../../../skills/memory-tasks/SKILL.md)
skill defines — kept identical here so the plugin and the skill agree on the
shape. For the full task workflow (creating, updating, completing), use that
skill.

Tasks are found by the SessionStart hook via structured recall:
`search_notes(metadata_filters={"type": "task", "status": "active"})`.

## Frontmatter vs observations

Put queryable fields (`status`, `priority`, `current_step`) in frontmatter so
`metadata_filters` can find them, and mirror them as `- [status] active`
observations so `schema_validate` sees them. `note_type="Task"` is stored as
lowercase `task` in frontmatter, so search with `note_types=["task"]`.
Validation is `warn` — advisory, never blocking.
