---
title: Session
type: schema
entity: Session
version: 1
schema:
  summary?: string, one-paragraph what-happened this session
  context?(array): string, key context needed to resume after memory loss
  next_step?(array): string, explicit cursor for the next session
  decision?(array): string, decisions surfaced during the session
  problem?(array): string, problems hit — including attempted-and-rejected approaches
  produced?(array): Entity, notes created or updated during the session
settings:
  validation: warn
  frontmatter:
    project: string, the Basic Memory project this session belongs to
    started: string, when the session began (ISO timestamp)
    ended?: string, when the session was checkpointed
    status?(enum, lifecycle of the checkpoint): [open, resumed, closed]
    cwd?: string, the working directory the session ran in
    claude_session_id?: string, Claude Code session identifier
    capture?(enum, how this checkpoint was produced): [extractive, summarized]
    repo?: string, repository or code project the session worked in
    branch?: string, git branch the session worked on
    git_sha?: string, commit sha the session ended on
    pr?: string, pull request or issue reference (e.g. "#1123")
---

# Session

A **SessionNote** is a resume checkpoint written by the Basic Memory plugin's
PreCompact hook (and, later, the `/basic-memory:bm-handoff` command) right before
Claude Code compacts the context window. It records what the session was doing
so the next session can pick up where this one left off.

Sessions are found by the SessionStart hook via structured recall:
`search_notes(metadata_filters={"type": "session"}, after_date="3d")`.

## What goes in a SessionNote

- **summary** — a short paragraph of what happened (richer once summarized
  checkpoints replace the extractive first cut).
- **context** / **next_step** — the cursor: what's in flight and what to do next.
- **decision** / **problem** — choices made and dead-ends hit, so the next
  session doesn't repeat them.
- **produced** — relations to the notes this session created or changed.

## Frontmatter

`type: session` and `status` are the queryable fields that power recall. `warn`
validation means a missing field is surfaced, never blocking — the user's flow
is never gated on schema conformance.

The git anchors (`repo`, `branch`, `git_sha`, `pr`) are the coding-setup fields:
`/basic-memory:bm-setup` seeds them when the project's focus is code/dev, so a
checkpoint pins exactly where the work happened and the next session can resume
on the right branch. Non-coding setups omit them.
