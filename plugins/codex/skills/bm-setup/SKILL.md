---
name: bm-setup
description: Set up Basic Memory for Codex at user or project level by mapping a Basic Memory project and seeding schemas.
---

# Basic Memory for Codex Setup

Set up the current repo so Codex can orient from Basic Memory and checkpoint work
back into it. Keep the interview short, but always ask before choosing where data
will be written.

## Preconditions

Confirm Basic Memory is reachable before changing files:

1. Prefer MCP: call `list_memory_projects`.
2. If MCP tools are not available, run `basic-memory --version` or `bm --version`.
3. If neither works, stop and tell the user to install Basic Memory and connect the
   MCP server. The plugin bundles an `.mcp.json` that starts `uvx basic-memory mcp`.
4. List available projects before the interview. Include cloud/local source,
   workspace, qualified name, and project id when available.

## Interview

Ask the user to choose the project mapping. Do not infer write targets from the
repo, default project, current directory, or previous local state.

- config level: user-level `~/.codex/basic-memory.json` or project-level
  `.codex/basic-memory.json`. Ask explicitly and recommend user level by default.
  Project settings override user settings key by key.
- storage mode: cloud, local, or mixed. Prefer the user's stated mode over any
  CLI default.
- `focus`: code/dev, research, writing, planning, or mixed.
- `sessionProfile`: `coding` or `general`. Recommend `coding` for code/dev. For
  mixed use, ask whether this repository should capture Git and pull-request
  context. Do not infer `coding` merely because the current directory is a Git
  checkout.
- `primaryProject`: an existing Basic Memory project or a new one to create.
- `secondaryProjects`: optional read-only projects for session-start context.
- `teamProjects`: optional share targets for `bm-share`.
- `captureFolder`: default `codex/<repo-dir>`, derived from the Git top-level
  directory. Ask only when the user wants an explicit override.
- `rememberFolder`: default `codex/remember`.
- `placementConventions`: a short note about where decisions, tasks, and research
  notes should land. Default decisions to `codex/decisions` so Codex-authored
  memory stays under one tree.
- `checkpointOnCompact`: whether post-compaction SessionStart asks Codex to run
  `bm-checkpoint`. Default to `true`; an explicit JSON boolean `false` opts out.
- `captureEvents`: whether to record lifecycle-event envelopes in the
  local hook inbox. Default to `true`; an explicit JSON boolean `false` opts out.

For the `coding` session profile, verify the current directory is inside a Git
repository. Resolve a stable `repository` identifier such as `owner/name` from
the current GitHub repository or origin remote, show it to the user, and ask for
confirmation. Do not guess when the remote is missing or ambiguous. Explain that
coding checkpoints store structured repository, branch, SHA, working-directory,
and optional pull-request metadata in Basic Memory.

Explain the capture tradeoff before asking: enabled capture adds a local event
trail that stays queued until `bm hook flush` archives it locally.
It never creates knowledge-graph notes or writes to team projects. The default is enabled; an explicit JSON boolean
`false` disables it, and malformed values fail closed.

If there are duplicate names, show qualified names and ask the user which one to
use. Prefer qualified project names or project ids for cloud projects. Never pick
between cloud and local variants without confirmation.

For a new or empty project, suggest a light convention instead of creating empty
folders. For an existing project, inspect `list_directory` and a few notes before
summarizing the real convention.

## Apply

After confirming the plan, write the shared settings to the chosen user-level or
project-level file:

```json
{
  "basicMemory": {
    "primaryProject": "<project-ref>",
    "secondaryProjects": [],
    "projectMode": "cloud",
    "teamProjects": {},
    "focus": "<focus>",
    "sessionProfile": "<general-or-coding>",
    "rememberFolder": "codex/remember",
    "recallTimeframe": "7d",
    "checkpointOnCompact": true,
    "captureEvents": true,
    "placementConventions": "Put decisions in codex/decisions/ and work checkpoints in codex/<repo-dir>/."
  }
}
```

Omit `captureFolder` to use `codex/<repo-dir>`; persist it only for an explicit
override. Preserve unrelated keys if the chosen file already exists. Include
`projectMode` when the user chose cloud, local, or mixed routing. Always persist
`checkpointOnCompact` and `captureEvents` as JSON booleans. These files are
intentionally Codex-specific; do not write `.claude/settings.json`.

For a user-level coding setup, omit `sessionProfile` from the shared user file and
keep both the coding profile and confirmed repository identifier in the project
file so neither can affect other repositories:

```json
{
  "basicMemory": {
    "sessionProfile": "coding",
    "repository": "owner/name"
  }
}
```

For a project-level setup, add `repository` to the shared settings in that same
project file.

Persist `sessionProfile` explicitly in the chosen file, except for a user-level
coding setup where it belongs in the project file alongside `repository`. Persist
`repository` only for the `coding` profile, after the user confirms it. A coding
setup is incomplete without a repository identifier because the `coding_session`
schema requires queryable Git identity fields.

## Seed Schemas

Read the schema files from `<plugin-root>/schemas/`. This skill lives at
`<plugin-root>/skills/bm-setup/SKILL.md`, so the schemas are two directories up.

Seed the session schema relevant to the selected profile into the chosen
`primaryProject` if it does not already exist:

- `coding-session.md` for `sessionProfile: coding`
- `codex-session.md` for `sessionProfile: general`

Then seed these schemas for both profiles:

- `decision.md`
- `task.md`

These schemas cover notes Codex writes directly. Lifecycle envelopes are not
notes: `bm hook flush` archives that operational trace locally, so there is no
projected session or tool-ledger schema to seed.

Use `write_note` with `directory="schemas"`, `note_type="schema"`, schema
frontmatter as metadata, and the markdown body as content. Do not paste the YAML
frontmatter into content.

Before seeding schemas, restate the exact target project and ask for confirmation
if it differs from the user's selected primary project or if routing is
ambiguous.

## Verify

Before closing, prove the mapping works:

- Search the primary project for `type=schema` with page size 10. For a coding
  setup, confirm the `Coding Session` schema is present.
- Search one shared project for open decisions if shared projects were configured.
- Run `basic-memory hook status --harness codex --project-dir <repo-root>` (using
  `bm` or `uvx basic-memory` if needed). Confirm that it finds this repo's
  settings, reports the selected project, session profile, repository, and
  intended checkpoint-prompt and capture states.
  Its inbox counts are shared across harnesses.
- If any check errors, fix the project ref or hook launcher before finishing.

Finish with the project mapping, schemas seeded or skipped, checkpoint prompt,
capture choice, shared inbox status, and the verification result.
Tell the user that plugin hooks need to be reviewed and trusted in Codex before
they run.
