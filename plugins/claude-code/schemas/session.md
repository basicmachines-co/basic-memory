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
    username?: string, operating-system user that created the checkpoint
    hostname?: string, host that created the checkpoint
    claude_session_id?: string, Claude Code session identifier
    capture?(enum, how this checkpoint was produced): [extractive, deliberate, summarized]
---

# Session

A **SessionNote** is a resume checkpoint. The Basic Memory plugin's PreCompact
hook writes one right before Claude Code compacts the context window, and the
`/basic-memory:bm-checkpoint` skill writes one deliberately. It records what the
session was doing so the next session can pick up where this one left off.

Sessions are found by the SessionStart hook via structured recall:
`search_notes(metadata_filters={"type": "session"}, after_date="3d")`.

In a **coding setup** (`sessionProfile: "coding"`), deliberate checkpoints use
the Coding Session schema instead — it adds required, queryable Git identity
(`repository`, `branch`, `git_sha`, pull-request fields). This schema stays the
general-purpose checkpoint.

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
