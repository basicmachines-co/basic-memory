---
name: bm-setup
description: Set up Basic Memory for Codex in the current repo by mapping a Basic Memory project, seeding schemas, and writing .codex/basic-memory.json.
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

- storage mode: cloud, local, or mixed. Prefer the user's stated mode over any
  CLI default.
- `focus`: code/dev, research, writing, planning, or mixed.
- `primaryProject`: an existing Basic Memory project or a new one to create.
- `secondaryProjects`: optional read-only projects for session-start context.
- `teamProjects`: optional share targets for `bm-share`.
- `captureFolder`: default `codex-sessions`.
- `rememberFolder`: default `codex-remember`.
- `placementConventions`: a short note about where decisions, tasks, and research
  notes should land.
- `captureEvents`: whether to record redacted lifecycle-event envelopes in the
  local hook inbox. Default to `false`; SessionStart briefs and PreCompact
  checkpoints work without it.
- `redactKeys` and `redactPaths`: optional additions to the built-in redaction
  floor. Ask for these only when event capture is enabled or the user has
  repo-specific privacy requirements.

Explain the capture tradeoff before asking: enabled capture adds a local,
redacted event trail that stays queued until `bm hook flush` projects it. It does
not write to team projects, and only the JSON boolean `true` enables it.

If there are duplicate names, show qualified names and ask the user which one to
use. Prefer qualified project names or project ids for cloud projects. Never pick
between cloud and local variants without confirmation.

For a new or empty project, suggest a light convention instead of creating empty
folders. For an existing project, inspect `list_directory` and a few notes before
summarizing the real convention.

## Apply

After confirming the plan, write `.codex/basic-memory.json` in the repo:

```json
{
  "basicMemory": {
    "primaryProject": "<project-ref>",
    "secondaryProjects": [],
    "projectMode": "cloud",
    "teamProjects": {},
    "focus": "<focus>",
    "captureFolder": "codex-sessions",
    "rememberFolder": "codex-remember",
    "recallTimeframe": "7d",
    "captureEvents": false,
    "redactKeys": [],
    "redactPaths": [],
    "placementConventions": "<short convention>"
  }
}
```

Preserve unrelated keys if the file already exists. Include `projectMode` when
the user chose cloud, local, or mixed routing. Always persist `captureEvents` as
a JSON boolean. Empty `redactKeys` and `redactPaths` lists may be omitted; when
present, they must be JSON arrays of strings. `redactKeys` extends payload-key
redaction, while `redactPaths` also protects working-directory and path-bearing
checkpoint content. This file is intentionally Codex-specific; do not write
`.claude/settings.json`.

## Seed Schemas

Read the schema files from `<plugin-root>/schemas/`. This skill lives at
`<plugin-root>/skills/bm-setup/SKILL.md`, so the schemas are two directories up.

Seed these schema notes into the chosen `primaryProject` if they do not already
exist:

- `codex-session.md`
- `decision.md`
- `task.md`

These schemas cover notes Codex writes directly. The normalized `session` and
`tool_ledger` artifacts written by `bm hook flush` are core-owned projections;
their shape belongs to the core projector and its tests, not duplicated plugin
schema files. They remain queryable by their `type` frontmatter.

Use `write_note` with `directory="schemas"`, `note_type="schema"`, schema
frontmatter as metadata, and the markdown body as content. Do not paste the YAML
frontmatter into content.

Before seeding schemas, restate the exact target project and ask for confirmation
if it differs from the user's selected primary project or if routing is
ambiguous.

## Verify

Before closing, prove the mapping works:

- Search the primary project for `type=schema` with page size 5.
- Search one shared project for open decisions if shared projects were configured.
- Run `basic-memory hook status --harness codex --project-dir <repo-root>` (using
  `bm` or `uvx basic-memory` if needed). Confirm that it finds this repo's
  settings, reports the selected project, and shows the intended capture state.
  Its inbox counts are shared across harnesses.
- If any check errors, fix the project ref or hook launcher before finishing.

Finish with the project mapping, schemas seeded or skipped, capture/redaction
choices, shared inbox status, and the verification result. Tell the user that
plugin hooks need to be reviewed and trusted in Codex before they run.
