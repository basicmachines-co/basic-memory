---
name: bm-decide
description: Capture a durable engineering decision in Basic Memory with rationale, alternatives, consequences, and affected work.
---

# Capture A Decision

Use this when the user makes or asks to record a durable choice. A decision is a
choice with rationale and consequences, not a casual preference.

## Steps

1. Resolve `~/.codex/basic-memory.json`, then the nearest project
   `.codex/basic-memory.json`; project keys override user keys:
   - write to `primaryProject` when set
   - follow `placementConventions` for the directory when they are specific
   - otherwise use `codex/decisions`

   Apply the `bm-writing` skill before drafting the note.

2. Clarify only if the choice itself is ambiguous. Do not ask for every field if
   the conversation already contains the rationale.

3. Write a `type: decision` note:
   - `status: open` unless the user says it is accepted, superseded, or rejected
   - `decided: <ISO timestamp when known>`
   - `project: <primaryProject if known>`

4. Include:
   - the decision
   - context
   - rationale
   - alternatives considered
   - consequences
   - affected files, specs, issues, PRs, or notes

5. Confirm with the permalink. If this supersedes an older decision, update the old
   note or link it as `supersedes`.
