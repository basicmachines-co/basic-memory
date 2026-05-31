---
name: setup
description: Set up the Basic Memory plugin for this project — a short guided interview that configures the project mapping, seeds note schemas, optionally learns the project's conventions, and enables capture reflexes. Use when the user runs /basic-memory:setup, says "set up basic memory", or asks to configure/bootstrap the plugin.
argument-hint: (no arguments — runs an interactive interview)
---

# Basic Memory setup

Run a short, adaptive interview (~2-3 minutes) and then write the configuration.
Be conversational and **skip questions whose answer is already obvious** from
context (e.g. if `list_memory_projects` shows a single local project and no cloud
workspaces, don't ask about cloud/teams — just confirm). Suggest a sensible default
for every question so the user can accept with one word. Don't do any writes until
the interview is done and you've confirmed the plan.

## Prerequisite check

First confirm the **Basic Memory MCP server is connected** — call
`list_memory_projects`. If that tool isn't available or errors, Basic Memory isn't
wired into Claude Code yet. **Stop and walk the user through it first** (everything
below depends on it):

1. Install it: `uv tool install basic-memory` (or `pip install basic-memory`),
   version `>= 0.19.0`.
2. Connect it: `claude mcp add basic-memory -- uvx basic-memory mcp`, then restart
   the session so the MCP server loads.

Re-check `list_memory_projects` before continuing — don't start the interview until
it succeeds.

## Interview

Ask only what you can't infer. Cover:

1. **Focus.** "What's this project mostly about — code, writing, research, planning,
   or a mix?" (Shapes folder suggestions later; one quick question.)

2. **Project mapping.** "Do you already have a Basic Memory project for this, or
   should I create one?"
   - Existing → show `list_memory_projects()` and let them pick. That name becomes
     `primaryProject`.
   - New → propose a name (default: this repo's directory name) and a path
     (default: `~/basic-memory/<name>/`), then create it with
     `create_memory_project`.

3. **Cloud / teams** (skip if there are no extra workspaces). Run
   `list_workspaces`. If the user belongs to more than one workspace, they likely
   have a **team** workspace alongside their personal/default one. Use
   `list_memory_projects` to see the projects in each (note: project names collide
   across workspaces, so always use the **workspace-qualified name**, e.g.
   `my-team-2/notes`, or the `external_id` UUID — never a bare name).
   - **Read from the team** (recommended): ask which team projects to pull into the
     session brief for recall. Store their qualified names in `secondaryProjects`.
     These are **read-only** — recall reads across them; nothing is written to them.
   - **Share target** (optional): if the user wants a place to *publish* notes to the
     team via `/basic-memory:share`, add it to `teamProjects` as
     `"<qualified-name>": { "promoteFolder": "shared" }`. Sharing is always a manual
     gesture — auto-capture never writes to a team project.

   Keep `primaryProject` a project the user owns for their *own* capture; team
   projects are for reading and deliberate sharing only.

4. **Learn conventions** (optional). "Want me to look at your existing notes and
   note your conventions so I place new notes consistently?" If yes, inspect the
   project: `list_directory` for the folder layout, sample a few notes per folder
   (and, where a folder holds recurring typed notes, you may run `schema_infer` to
   see their shape). Summarize what you find — folder-by-topic conventions, naming
   style, the observation categories they favor — into 3-6 short lines and store
   that string as `placementConventions`. Infer from their *real* notes; don't
   impose a structure.

5. **Schemas.** "I'll add schemas for session checkpoints, decisions, and tasks so
   I can find them precisely later — okay?" (See "Seed the schemas" below.)

6. **How active should I be? (output style)** "Want me to proactively capture —
   search the graph before recalling, write material decisions as typed notes, and
   cite permalinks? Or keep it quiet (just the session brief, the PreCompact
   checkpoint, and `/basic-memory:remember` on demand)?" Enabling it sets
   `outputStyle: "basic-memory"`. Default to enabled; leave it off for a recall-only,
   low-noise setup. (This is the single knob for how proactive the assistant is —
   the hooks always run regardless.)

7. **Shared skills** (optional, default yes). "Want the full Basic Memory toolkit —
   the shared `memory-*` skills (`memory-notes`, `memory-tasks`, `memory-research`,
   `memory-schema`, `memory-defrag`, …)? I can install them alongside this plugin."
   These are the canonical, framework-agnostic skills (the same set OpenClaw bundles).
   This plugin ships only the Claude-Code-specific glue and pulls the shared set on
   demand — it doesn't vendor its own copies. (See "Install the shared skills" below.)

## Apply (after confirming the plan)

### 1. Seed the schemas
The plugin ships seed schemas at `<plugin>/schemas/` — that's **two directories up
from this skill's directory, then `schemas/`** (this skill is at
`<plugin>/skills/setup/`). Read `session.md`, `decision.md`, and `task.md` there.

For each one:
- Check whether the chosen project already has a schema for that type
  (`search_notes` with `metadata_filters={"type": "schema"}`, or try
  `read_note("schemas/<name>")`). **If it exists, skip it** — never overwrite a
  schema the user may have customized.
- Otherwise write it with `write_note`: `directory="schemas"`,
  `title` = the schema's title (Session / Decision / Task), `content` = the file's
  full contents (including its `---` frontmatter — Basic Memory merges that into the
  note's frontmatter, so the `type: schema` + `entity` + `schema` definition land
  intact and become resolvable by `schema_validate`), routed to `primaryProject`
  (pass it as `project`, or as `project_id` if it's an `external_id` UUID).

### 2. Install the shared skills (if the user opted in)
Run, from the project root:

```
npx skills add basicmachines-co/basic-memory --path skills
```

This installs the canonical `memory-*` skills into the user's skills directory — the
single source of truth, shared with OpenClaw. The plugin does **not** vendor copies;
it relies on this shared set. If `npx` / the `skills` CLI isn't available, point the
user at the manual install in the top-level [`skills/README.md`](../../../../skills/README.md).

### 3. Write settings
Build the `basicMemory` block from the interview:

```json
{
  "basicMemory": {
    "primaryProject": "<chosen>",
    "secondaryProjects": [],
    "captureFolder": "sessions",
    "rememberFolder": "bm-remember",
    "recallTimeframe": "3d",
    "preCompactCapture": "extractive",
    "placementConventions": "<inferred summary, or null>",
    "teamProjects": {}
  },
  "outputStyle": "basic-memory"
}
```
Only include `outputStyle` if the user opted in. Ask whether this is a **team
default** (write/merge into `.claude/settings.json`, suggest committing it) or
**personal** (`.claude/settings.local.json`). **Merge** into any existing file —
read it, add/replace only the keys above, preserve everything else. Use compact,
valid JSON.

Writing the `basicMemory` block is also what stops the SessionStart hook's first-run
nudge — the config's presence is the signal that setup has run.

## Close

Confirm what you did in a few lines: the project mapping, which schemas were seeded
vs. already present, whether conventions were learned, and whether the output style
is on. End with: *"Done — I'll use this from the next message. Run
`/basic-memory:status` anytime to see what I'm tracking."* Note that the output
style (if enabled) takes effect on the next session, since it's fixed at startup.
