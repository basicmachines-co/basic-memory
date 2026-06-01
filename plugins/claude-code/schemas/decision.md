---
title: Decision
type: schema
entity: Decision
version: 1
schema:
  decision: string, the choice that was made
  rationale?: string, why this choice over the alternatives
  alternative?(array): string, options that were considered and not taken
  consequence?(array): string, what this decision commits us to
  context?: string, the situation that prompted the decision
  affects?(array): Entity, work or notes this decision bears on
  supersedes?: Entity, a prior decision this one replaces
settings:
  validation: warn
  frontmatter:
    status?(enum, lifecycle of the decision): [open, accepted, superseded, rejected]
    decided?: string, when the decision was made (ISO timestamp)
    project?: string, the Basic Memory project this decision belongs to
---

# Decision

A **DecisionNote** is a durable record of a real choice — one with alternatives
and a rationale, not a passing preference. The Basic Memory plugin's output-style
prompts Claude to capture these inline as decisions are made, and the future
`/basic-memory:bm-decide` command captures them explicitly.

Decisions are found by structured recall:
`search_notes(metadata_filters={"type": "decision", "status": "open"})`.

## What makes a good DecisionNote

- **decision** — state the choice plainly.
- **rationale** + **alternative** — why this, and what was rejected. This is the
  part that saves a future session from relitigating the same ground.
- **consequence** — what the choice commits the work to.
- **affects** / **supersedes** — relations that wire the decision into the graph.

## Frontmatter

`type: decision` plus `status` make decisions queryable. Capture decisions
sparingly — one note per genuine decision, not per opinion. Validation is `warn`,
never blocking.
