---
title: Tool Ledger
type: schema
entity: ToolLedger
version: 1
schema:
  summary?: string, what tools were used and their overall outcome
  tool_call?(array): string, tool name with abbreviated args summary
  tool_result?(array): string, tool outcome summary (success or failure reason)
  file_changed?(array): string, paths created or modified by tool calls
  decision?(array): string, decisions made based on tool results
settings:
  validation: warn
  frontmatter:
    project: string, the Basic Memory project this ledger belongs to
    session_id?: string, harness session identifier
    started: string, when the first tool call was recorded
    ended?: string, when the last tool call was recorded
    status?(enum, lifecycle of the ledger): [open, closed]
    type: tool_ledger
    source?: string, harness source (claude-code or codex)
    idempotency_key?: string, dedup key from the producer envelope
---

# Tool Ledger

A **ToolLedger** records the sequence of tool calls and their outcomes during
an agent session. It complements a SessionNote by capturing the *mechanical*
work — which tools were invoked, what files they touched, what failed — rather
than the *narrative* summary of what happened.

ToolLedger notes are found by structured recall:
`search_notes(metadata_filters={"type": "tool_ledger"}, after_date="7d")`.

## What Goes In A ToolLedger

- **summary** — one paragraph of what the tool sequence accomplished.
- **tool_call** — each significant tool invocation with abbreviated arguments.
- **tool_result** — the outcome of each call (pass/fail/partial).
- **file_changed** — paths touched, useful for resume and conflict detection.
- **decision** — any decisions that emerged from tool results.

## When It's Written

V0 defines the schema for forward compatibility. Actual ToolLedger notes will
be produced when PostToolUse hooks become available (v1). For now, tool-level
observations can be included in CodexSession checkpoints.

Validation is `warn` so ledger creation never blocks the user's flow.
