---
title: Decision
type: schema
entity: Decision
version: 1
schema:
  decision: string, the choice that was made
  rationale?: string, why this choice over alternatives
  alternative?(array): string, options considered and not taken
  consequence?(array): string, what this decision commits the work to
  context?: string, the situation that prompted the decision
  affects?(array): Entity, work or notes this decision bears on
  supersedes?: Entity, a prior decision this one replaces
settings:
  validation: warn
  frontmatter:
    status?(enum, lifecycle of the decision): [open, accepted, superseded, rejected]
    decided?: string, when the decision was made
    project?: string, the Basic Memory project this decision belongs to
---

# Decision

A **Decision** note records a real choice with rationale and consequences. Codex
uses decisions to avoid relitigating the same tradeoff in later threads.

Decisions are found by structured recall:
`search_notes(metadata_filters={"type": "decision", "status": "open"})`.

Capture decisions sparingly. Use one note per genuine durable choice.
