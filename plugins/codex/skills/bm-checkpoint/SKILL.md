---
name: bm-checkpoint
description: Create an immutable Codex handoff in Basic Memory and return an exact bm-orient resume command.
---

# Checkpoint Codex Work

Create a durable, immutable handoff note for current Codex work. Use this when
the user asks to checkpoint, wrap up, hand off, remember the work state, or when
the post-compaction SessionStart context requests the deliberate handoff.

## Gather

Read `~/.codex/basic-memory.json`, then the nearest project
`.codex/basic-memory.json`; project keys override user keys:

- `primaryProject`, default omitted
- `captureFolder`, default `codex/<git top-level directory name>`
- `placementConventions`, optional
- `sessionProfile`, default `general`
- `repository`, required when `sessionProfile` is `coding`

Apply the `bm-writing` skill before drafting the note.

Gather repo evidence:

- the original objective that started the thread and why it mattered
- the latest user intent, including corrections or scope changes that supersede
  the original objective
- the approach taken and why it solves the problem
- the current system state and practical impact
- tradeoffs, sharp edges, useful simplifications, and intentionally parked work
- `git status --short`
- current branch
- repository root and current working directory
- current Git SHA
- current pull request number, title, URL, state, base, and head when one exists
- changed files you touched
- tests or checks actually run
- failures or skipped checks
- decisions made in this thread
- unresolved blockers
- next action
- current username, hostname, and timestamp

Use direct, read-only evidence for repository and pull-request state. Do not
claim a test passed unless you ran it or the user supplied the result.

## Write

A checkpoint is a durable handoff, not a status dump or commit-by-commit
changelog. Tell the story for a human or agent returning later. Treat it as a
snapshot plus pointers to authoritative artifacts, not a replacement for tasks,
decisions, plans, issues, pull requests, commits, diffs, checked-in docs, or
source files.

Every invocation creates a new checkpoint. Never edit, replace, or append to an
earlier checkpoint, even when the topic is unchanged.

Use the title:

`Codex checkpoint - <UTC YYYY-MM-DDTHH-MM-SSZ> - <short topic>`

The UTC timestamp is part of the immutable checkpoint identity and avoids
filename-unsafe colons. If `write_note` reports a title collision, retry with
the smallest available numeric suffix such as ` - 2`. Never resolve a collision
by modifying the existing note.

Call `write_note` with `project=<configured primaryProject>`,
`overwrite=False`, and `output_format="json"` on every attempt. When
`primaryProject` is omitted, leave the project argument unset so Basic Memory
uses its default project. The frontmatter `project` field is descriptive
metadata and does not replace the tool's project argument. The explicit
non-overwrite flag must win even when the user's
`write_note_overwrite_default` setting is true. Only accept a successful result
with `action: created`; treat `action: conflict` or `NOTE_ALREADY_EXISTS` as the
title collision above, and stop on any other action or error.

Write a note to Basic Memory. For the `general` profile:

- `title`: the timestamped checkpoint title above
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

For the `coding` profile, write `type: coding_session` and use the same common
frontmatter plus these schema-required fields:

- `repository: <confirmed stable repository identifier>`
- `repo_root: <git rev-parse --show-toplevel>`
- `cwd: <current cwd>`
- `branch: <git rev-parse --abbrev-ref HEAD>`
- `git_sha: <git rev-parse HEAD>`

When the current branch has a pull request, also add the typed optional fields
`pull_request_number`, `pull_request_title`, `pull_request_url`,
`pull_request_state`, `pull_request_base`, and `pull_request_head`. Resolve the
pull request with a read-only GitHub query; omit those fields when no PR exists.
Write the number as a quoted string, for example `pull_request_number: "123"`,
so exact metadata queries behave consistently across storage backends.
Never infer or copy repository/PR identity only from conversation text. Stop if
the required coding fields cannot be proven.

Begin the body with `# <exact note title>`.

Use these sections, omitting optional ones that add no value:

- `## Summary`: one concrete sentence that does not merely repeat the title
- `## Story`: original objective -> latest user intent -> approach -> current
  state and impact in substantive prose
- `## Working State`: separate durable state from machine-local or fragile state
- `## Changed Files`, when paths are useful for resuming
- `## Verification`, for checks actually run and their outcomes
- `## Observations`
- `## Relations`, when the thread has an obvious graph target

Prefer repository-relative paths in the body. Required absolute `repo_root` and
`cwd` frontmatter remain machine-local evidence. Label dirty or untracked files,
ignored files, active processes, dev servers, temporary directories, and local
tool caches as machine-local or fragile when they matter to resumption. Do not
present them as durable project state.

Make the note pointer-first:

- name authoritative artifacts and include their stable identifiers or links
- summarize only the context needed to understand why each pointer matters
- use a relation for an existing graph note and a normal link or repository
  path for artifacts outside the graph
- do not copy large plans, diffs, logs, or source files into the checkpoint

Use observations to distill durable facts for structured recall rather than
duplicating every narrative sentence:

- `[result]` for concrete outcomes
- `[decision]` for each decision made or preserved
- `[blocker]` for each unresolved blocker
- `[next_step]` for the one primary next action; include exactly one
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

Reply with:

1. one sentence summarizing what the checkpoint preserves
2. the exact resume identifier selected from the successful JSON result
3. the one primary next action
4. exactly one fenced resume command as the final block:

```text
$bm-orient "<exact returned resume identifier>"
```

Choose the first non-empty returned value in this order: `permalink`,
`file_path`, then `title`. This preserves a direct resume cursor when the Basic
Memory project has permalinks disabled. Use the returned value verbatim; never
construct or guess a permalink or file path.
