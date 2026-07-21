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

Apply the `bm-writing` skill before drafting the note.

Gather repo evidence:

- the original problem or goal and why it mattered
- the approach taken and why it solves the problem
- the current system state and practical impact
- tradeoffs, sharp edges, useful simplifications, and intentionally parked work
- `git status --short`
- current branch
- changed files you touched
- tests or checks actually run
- failures or skipped checks
- decisions made in this thread
- unresolved blockers
- next action
- current username, hostname, and timestamp

Do not claim a test passed unless you ran it or the user supplied the result.

## Write

A checkpoint is a durable handoff, not a status dump or commit-by-commit
changelog. Tell the story for a human or agent returning later.

Write a note to Basic Memory:

- `title`: `Codex checkpoint - <short topic>`
- `directory`: configured `captureFolder`
- `tags`: `["codex", "checkpoint"]`
- frontmatter:
  - `type: codex_session`
  - `status: open`
  - `project: <primaryProject if known>`
  - `cwd: <current cwd>`
  - `started: <current timestamp>`
  - `username: <current username>`
  - `hostname: <current hostname>`
  - `capture: deliberate`

Begin the body with `# <exact note title>`.

Use these sections, omitting optional ones that add no value:

- `## Summary`: one concrete sentence that does not merely repeat the title
- `## Story`: problem -> approach -> current state and impact in substantive prose
- `## Changed Files`, when paths are useful for resuming
- `## Verification`, for checks actually run and their outcomes
- `## Observations`
- `## Relations`, when the thread has an obvious graph target

Use observations to distill durable facts for structured recall rather than
duplicating every narrative sentence:

- `[result]` for concrete outcomes
- `[decision]` for each decision made or preserved
- `[blocker]` for each unresolved blocker
- `[next_step]` for the next concrete action; include at least one
- `[verification]` or `[changed_file]` only when the item is itself important
  project memory, not merely supporting detail

Do not create separate Decisions, Blockers, or Next Action sections with plain
bullets. Omit empty categories instead of writing placeholder text such as
"None."

Relations are not observations. Put them under `## Relations` using Basic
Memory relation syntax, for example `- relates_to [[Exact existing note title]]`.
Never write `[relates_to]` or a bare `memory://` URL as an observation. Only add
a relation when its target is an existing task, decision, spec, issue, or PR note.

## Confirm

Reply with the permalink and the one next action the checkpoint preserves.
