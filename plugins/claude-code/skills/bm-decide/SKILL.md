---
name: bm-decide
description: Capture a durable engineering decision in Basic Memory with rationale, alternatives, consequences, and affected work. Use when the user makes or asks to record a real choice — "let's go with X", "record this decision", or runs /basic-memory:bm-decide.
argument-hint: (optional short statement of the decision)
---

# Capture A Decision

Use this when the user makes or asks to record a durable choice. A decision is a
choice with rationale and consequences, not a casual preference. This is the
deliberate version of the basic-memory output style's decision-capture reflex —
it works whether or not that style is enabled.

## Steps

1. Resolve config: read the `basicMemory` block with the same precedence the
   hooks use — user-level `~/.claude/settings.json` as the base, then the
   project's `.claude/settings.json` and `.claude/settings.local.json` override
   it per key:
   - write to `primaryProject` when set (pass it as `project`, or as
     `project_id` if it's an `external_id` UUID)
   - follow `placementConventions` for the directory when they are specific
   - otherwise use `decisions`

   Apply the `bm-writing` skill before drafting the note.

2. Clarify only if the choice itself is ambiguous. Do not ask for every field if
   the conversation already contains the rationale.

3. Write a `type: decision` note (`note_type: decision`):
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
