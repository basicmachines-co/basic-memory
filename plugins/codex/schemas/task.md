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
  current_step?: integer, which step number is current
  context?: string, key context needed to resume
  started?: string, when work began
  completed?: string, when work finished
  blockers?(array): string, what prevents progress
  parent_task?: Task, parent task if this is a subtask
settings:
  validation: warn
---

# Task

A **Task** note tracks work in progress so Codex can find it on the next thread.
It matches the framework-agnostic `memory-tasks` shape.

Tasks are found by structured recall:
`search_notes(metadata_filters={"type": "task", "status": "active"})`.

Put queryable fields such as `status` and `current_step` in frontmatter, and use
observations for human-readable progress notes.
