/**
 * Canonical Task schema note content, seeded into new projects on first startup.
 * If the schema already exists (user may have customized it), seeding is skipped.
 */
export const TASK_SCHEMA_CONTENT = `---
title: Task
type: schema
entity: Task
version: 1
schema:
  description: "string, what needs to be done"
  status?(enum): "[active, blocked, done, abandoned], current state"
  assigned_to?: "string, who is working on this"
  steps?(array): "string, ordered steps to complete"
  current_step?: "integer, which step number we're on (1-indexed)"
  context?: "string, key context needed to resume after memory loss"
  started?: "string, when work began"
  completed?: "string, when work finished"
  blockers?(array): "string, what's preventing progress"
  parent_task?: "Task, parent task if this is a subtask"
settings:
  validation: warn
---

# Task

Structured task note for tracking multi-step work that survives context compaction.

## Observations
- [convention] Task files live in memory/tasks/ with format YYYY-MM-DD-short-name.md
- [convention] Status transitions: active → blocked → done or abandoned
- [convention] Include a ## Context section for resumption after memory loss
- [convention] Include a ## Steps section with numbered checkbox items for step tracking
`
