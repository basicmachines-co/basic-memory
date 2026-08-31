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
compaction and shows up in the next session's brief.

Put queryable fields (`status`, `current_step`) in frontmatter so metadata
filters can find them. `type` is stored as lowercase `task` in frontmatter.
New tasks start at `current_step: 1` with `status: active`.

## Relations

- example [[Migrate CI to uv]]
