---
title: Codex Session
type: schema
entity: CodexSession
version: 1
schema:
  summary?: string, one-paragraph what happened in this Codex thread
  changed_file?(array): string, files created, edited, deleted, or inspected
  verification?(array): string, checks run and their result
  decision?(array): string, decisions surfaced or created during the thread
  blocker?(array): string, unresolved blockers or failed approaches
  next_step?(array): string, explicit cursor for the next Codex thread
  produced?(array): Entity, notes or artifacts created or updated
settings:
  validation: warn
  frontmatter:
    project: string, the Basic Memory project this session belongs to
    started: string, when the session began or checkpoint was created
    ended?: string, when the session was checkpointed
    status?(enum, lifecycle of the checkpoint): [open, resumed, closed]
    cwd?: string, working directory for the Codex thread
    username?: string, operating-system user that created the checkpoint
    hostname?: string, host that created the checkpoint
    codex_session_id?: string, Codex session identifier
    codex_turn_id?: string, Codex turn identifier
    trigger?: string, compaction trigger or deliberate checkpoint source
    model?: string, active Codex model slug when known
    capture?(enum, how this checkpoint was produced): [extractive, deliberate, summarized]
---

# Codex Session

A **CodexSession** note is a resumable engineering checkpoint. It captures the
thread cursor: what changed, what was verified, what decisions matter, and what
the next Codex thread should do first.

Codex sessions are found by structured recall:
`search_notes(metadata_filters={"type": "codex_session"}, after_date="7d")`.

## What Goes In A CodexSession

- **summary** - what happened.
- **changed_file** - changed or inspected paths that matter to resume.
- **verification** - commands actually run and their outcome.
- **decision** - choices made or surfaced.
- **blocker** - open failures, constraints, or rejected approaches.
- **next_step** - the next concrete action.

Validation is `warn` so checkpointing never blocks the user's flow.
