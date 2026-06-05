---
name: bm-checkpoint
description: Save a deliberate Codex work checkpoint to Basic Memory with changed files, verification, decisions, blockers, and the next action.
---

# Checkpoint Codex Work

Create a durable handoff note for current Codex work. Use this when the user asks
to checkpoint, wrap up, hand off, remember the state of the work, or before a long
context transition.

## Gather

Read `.codex/basic-memory.json` if present:

- `primaryProject`, default omitted
- `captureFolder`, default `codex-sessions`
- `placementConventions`, optional

Gather repo evidence:

- `git status --short`
- current branch
- changed files you touched
- tests or checks actually run
- failures or skipped checks
- decisions made in this thread
- unresolved blockers
- next action

Do not claim a test passed unless you ran it or the user supplied the result.

## Write

Write a note to Basic Memory:

- `title`: `Codex checkpoint - <short topic>`
- `directory`: configured `captureFolder`
- `tags`: `["codex", "checkpoint"]`
- frontmatter:
  - `type: codex_session`
  - `status: open`
  - `project: <primaryProject if known>`
  - `cwd: <current cwd>`
  - `capture: deliberate`

Use sections:

- Summary
- Changed Files
- Verification
- Decisions
- Blockers
- Next Action
- Observations

Observations should include at least one `[next_step]` line. Add relations to
existing tasks, decisions, specs, issues, or PRs when the thread has obvious ones.

## Confirm

Reply with the permalink and the one next action the checkpoint preserves.
