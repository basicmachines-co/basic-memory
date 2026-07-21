---
name: bm-checkpoint
description: Save a deliberate work checkpoint to Basic Memory with the story, changed files, verification, decisions, blockers, and the next action. Use when the user asks to checkpoint, wrap up, hand off, or remember the state of the work.
argument-hint: (optional short topic for the checkpoint title)
---

# Checkpoint Claude Work

Create a durable handoff note for the current work. Use this when the user asks
to checkpoint, wrap up, hand off, remember the state of the work, or before a
long context transition. This is the deliberate, high-signal counterpart to the
automatic PreCompact checkpoint.

## Gather

Resolve config: read the `basicMemory` block with the same precedence the hooks
use — user-level `~/.claude/settings.json` as the base, then the project's
`.claude/settings.json` and `.claude/settings.local.json` override it per key:

- `primaryProject`, default omitted (Basic Memory's default project)
- `captureFolder`, default `sessions`
- `placementConventions`, optional
- `sessionProfile`, default `general`
- `repository`, required when `sessionProfile` is `coding`

Apply the `bm-writing` skill before drafting the note.

Gather evidence:

- the original problem or goal and why it mattered
- the approach taken and why it solves the problem
- the current system state and practical impact
- tradeoffs, sharp edges, useful simplifications, and intentionally parked work
- the durable lesson, if one exists — what future work should know or avoid
- `git status --short`
- current branch
- repository root and current working directory
- current Git SHA
- current pull request number, title, URL, state, base, and head when one exists
- changed files you touched
- tests or checks actually run
- failures or skipped checks
- decisions made in this session
- unresolved blockers
- next action
- current username, hostname, and timestamp

Do not claim a test passed unless you ran it or the user supplied the result.

## Write

A checkpoint is a durable handoff, not a status dump or commit-by-commit
changelog. Tell the story for a human or agent returning later.

Write the note with `write_note`, routed to `primaryProject` (pass it as
`project`, or as `project_id` if it's an `external_id` UUID). For the `general`
profile:

- `title`: `Claude checkpoint - <short topic>`
- `directory`: configured `captureFolder`
- `tags`: `["claude", "checkpoint"]`
- `note_type`: `session`
- `metadata` (frontmatter):
  - `status: open`
  - `project: <primaryProject if known>`
  - `cwd: <current cwd>`
  - `started: <current timestamp>`
  - `username: <current username>`
  - `hostname: <current hostname>`
  - `capture: deliberate`

For the `coding` profile, write `note_type: coding_session` (frontmatter
`type: coding_session`) and use the same common frontmatter plus these
schema-required fields:

- `repository: <confirmed stable repository identifier>`
- `repo_root: <git rev-parse --show-toplevel>`
- `cwd: <current cwd>`
- `branch: <git rev-parse --abbrev-ref HEAD>`
- `git_sha: <git rev-parse HEAD>`

When the current branch has a pull request, also add the typed optional fields
`pull_request_number`, `pull_request_title`, `pull_request_url`, lowercase
`pull_request_state`, `pull_request_base`, and `pull_request_head`. Resolve the
pull request with a read-only GitHub query (e.g. `gh pr view --json ...`); omit
those fields when no PR exists. Write the number as a quoted string, for example
`pull_request_number: "123"`, so exact metadata queries behave consistently
across storage backends. Never infer or copy repository/PR identity only from
conversation text. Stop if the required coding fields cannot be proven.

Begin the body with `# <exact note title>`.

Use these sections, omitting optional ones that add no value:

- `## Summary`: one concrete sentence that does not merely repeat the title
- `## Story`: problem -> approach -> current state and impact in substantive prose
- `## Project Memory`, when the work surfaced a durable lesson future readers
  need — the constraint discovered, the boundary made explicit, the shortcut to
  avoid
- `## Changed Files`, when paths are useful for resuming
- `## Verification`, for checks actually run and their outcomes
- `## Observations`
- `## Relations`, when the session has an obvious graph target

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
