---
title: Codex Session
type: schema
entity: CodexSession
version: 1
schema:
  summary?: string, one-paragraph what happened in this coding thread
  changed_file?(array): string, files created, edited, deleted, or inspected
  verification?(array): string, checks run and their result
  decision?(array): string, decisions surfaced or created during the thread
  blocker?(array): string, unresolved blockers or failed approaches
  next_step?(array): string, explicit cursor for the next coding thread
  produced?(array): Entity, notes or artifacts created or updated
settings:
  validation: warn
  frontmatter:
    project: string, the Basic Memory project this session belongs to
    started: string, when the session began or checkpoint was created
    repo: string, stable repository identifier such as owner/name
    repo_root: string, Git repository root for this checkout
    cwd: string, working directory for the Codex thread
    branch: string, checked-out Git branch or HEAD when detached
    git_sha: string, exact Git commit at checkpoint time
    ended?: string, when the session was checkpointed
    status?(enum, lifecycle of the checkpoint): [open, resumed, closed]
    pr?: string, current pull request reference such as "#123"
    pr_title?: string, current pull request title
    pr_url?: string, canonical pull request URL
    pr_state?(enum, pull request state at checkpoint time): [open, closed, merged]
    pr_base?: string, pull request base branch
    pr_head?: string, pull request head branch
    username?: string, operating-system user that created the checkpoint
    hostname?: string, host that created the checkpoint
    codex_session_id?: string, Codex session identifier
    codex_turn_id?: string, Codex turn identifier
    trigger?: string, compaction trigger or deliberate checkpoint source
    model?: string, active Codex model slug when known
    capture?(enum, how this checkpoint was produced): [extractive, deliberate, summarized]
---

# Codex Session

This is the coding-setup variant of the **CodexSession** schema. It keeps the
same `type: codex_session` used by general and legacy Codex checkpoints while
requiring enough Git identity to resume repository work precisely.

Only `bm-setup` coding profiles seed this variant. General profiles seed
`codex-session.md` instead, so each Basic Memory project has one CodexSession
schema and one structured recall shape.

Examples:

`search_notes(note_types=["codex_session"], metadata_filters={"repo": "owner/repo"})`

`search_notes(note_types=["codex_session"], metadata_filters={"pr": "#123"})`

Pull-request fields are optional because valid coding work can precede a pull
request. When a pull request exists, checkpoint writers populate the complete
pull-request field set.
