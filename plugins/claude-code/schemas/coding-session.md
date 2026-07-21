---
title: Coding Session
type: schema
entity: CodingSession
version: 1
schema:
  summary?: string, one-paragraph what happened in this coding session
  changed_file?(array): string, files created, edited, deleted, or inspected
  verification?(array): string, checks run and their result
  decision?(array): string, decisions surfaced or created during the session
  blocker?(array): string, unresolved blockers or failed approaches
  next_step?(array): string, explicit cursor for the next coding session
  produced?(array): Entity, notes or artifacts created or updated
settings:
  validation: warn
  frontmatter:
    project: string, the Basic Memory project this session belongs to
    started: string, when the session began or checkpoint was created
    repository: string, stable repository identifier such as owner/name
    repo_root: string, Git repository root for this checkout
    cwd: string, working directory for the session
    branch: string, checked-out Git branch or HEAD when detached
    git_sha: string, exact Git commit at checkpoint time
    ended?: string, when the session was checkpointed
    status?(enum, lifecycle of the checkpoint): [open, resumed, closed]
    pull_request_number?: integer, current pull request number
    pull_request_title?: string, current pull request title
    pull_request_url?: string, canonical pull request URL
    pull_request_state?(enum, pull request state at checkpoint time): [open, closed, merged]
    pull_request_base?: string, pull request base branch
    pull_request_head?: string, pull request head branch
    username?: string, operating-system user that created the checkpoint
    hostname?: string, host that created the checkpoint
    claude_session_id?: string, Claude Code session identifier
    trigger?: string, compaction trigger or deliberate checkpoint source
    model?: string, active model slug when known
    capture?(enum, how this checkpoint was produced): [extractive, deliberate, summarized]
---

# Coding Session

A **CodingSession** is a resumable engineering checkpoint whose repository
identity is structured and queryable. Required Git fields make it possible to
find the exact work cursor without parsing prose.

Examples:

`search_notes(note_types=["coding_session"], metadata_filters={"repository": "owner/repo"})`

`search_notes(note_types=["coding_session"], metadata_filters={"pull_request_number": 123})`

Pull-request fields are optional because valid coding work can precede a pull
request. When a pull request exists, checkpoint writers populate the complete
pull-request field set.
