---
name: bm-setup
description: Set up the Basic Memory plugin for this project — a short guided interview that configures the project mapping, seeds note schemas, learns or suggests placement conventions, and enables capture reflexes. Use when the user runs /basic-memory:bm-setup, says "set up basic memory", or asks to configure/bootstrap the plugin.
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

1. **Focus / how you'll use it.** "What will this project mostly be — code/dev,
   research, writing, knowledge capture, planning, or a mix?" This answer is
   **load-bearing**, not small talk: it drives the folder structure you suggest
   (step 4) and is stored so the SessionStart brief can surface it, keeping capture
   matched to the use-case. Don't let it evaporate — if you infer it from context
   instead of asking, still say the use-case you assumed and the structure it
   implies, and let the user correct it in one word.

2. **Project mapping.** "Do you already have a Basic Memory project for this, or
   should I create one?"
   - Existing → show `list_memory_projects()` and let them pick. That name becomes
     `primaryProject`.
   - New → propose a name (default: this repo's directory name) and create it with
     `create_memory_project`.
     - *Local project* (default): path defaults to `~/basic-memory/<name>/`; any
       connected Basic Memory server can create it.
     - *Cloud project* (the user wants capture in a cloud workspace): pass the
       `workspace` selector (a slug from `list_workspaces`) and a cloud-style path
       like `/<name>`, and create it with a **cloud-connected** MCP server. A purely
       local server (`uvx basic-memory mcp`) treats the path as a local directory and
       fails to create it (e.g. read-only `/`). When both a local and a cloud server
       are connected, route creation *and* the schema seeding through the cloud one,
       and pin `primaryProject` to the new project's `external_id` UUID
       (collision-proof across workspaces).

3. **Cloud / teams** (skip if there are no extra workspaces). Run
   `list_workspaces`. If the user belongs to more than one workspace, they likely
   have a **team** workspace alongside their personal/default one. Use
   `list_memory_projects` to see the projects in each (note: project names collide
   across workspaces, so always use the **workspace-qualified name**, e.g.
   `my-team-2/notes`, or the `external_id` UUID — never a bare name).
   - **Read from the team** (recommended): ask which team projects to pull into the
     session brief for recall. Store their qualified names in `secondaryProjects`.
     These are **read-only** — recall reads across them; nothing is written to them.
     **Cap:** the SessionStart brief reads only the first **6** shared projects per
     session (a latency/output bound), in list order. If the user wants more than
     six, order the most relevant first and tell them the rest are configured but
     not read each session.
   - **Share target** (optional): if the user wants a place to *publish* notes to the
     team via `/basic-memory:bm-share`, add it to `teamProjects` as
     `"<qualified-name>": { "promoteFolder": "shared" }`. Sharing is always a manual
     gesture — auto-capture never writes to a team project.

   Keep `primaryProject` a project the user owns for their *own* capture; team
   projects are for reading and deliberate sharing only.

4. **Placement — learn or suggest** (depends on the project's state). The goal is a
   short `placementConventions` string (3-6 lines) telling you where new notes go.
   How you get it depends on whether the project already has notes:
   - **Existing project with notes** → *learn*. Inspect it: `list_directory` for the
     folder layout, sample a few notes per folder (and, where a folder holds
     recurring typed notes, you may run `schema_infer` to see their shape).
     Summarize the *real* conventions — folder-by-topic layout, naming style, the
     observation categories they favor. Infer from their actual notes; don't impose.
   - **New or empty project** → *suggest* (there's nothing to learn yet). Propose a
     **light** structure that fits the focus from step 1 — 3-5 optional top-level
     folders, no deep taxonomy — and be explicit that it's a starting point, not a
     scaffold: notes work fine without it and structure can stay emergent. Don't
     create empty folders; folders appear as notes land in them. Let the user edit
     or decline in one word.
   Either way, keep it short and store the result as `placementConventions`. The
   SessionStart brief surfaces it (alongside `captureFolder`), so this is what makes
   your captures land where the user expects — without it, placement is guesswork.

5. **Schemas.** "I'll add schemas for session checkpoints, decisions, and tasks so
   I can find them precisely later — okay?" (See "Seed the schemas" below.)

6. **How active should I be? (output style)** "Want me to proactively capture —
   search the graph before recalling, write material decisions as typed notes, and
   cite permalinks? Or keep it quiet (just the session brief, the PreCompact
   checkpoint, and `/basic-memory:bm-remember` on demand)?" Enabling it sets
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
`<plugin>/skills/bm-setup/`). Read `session.md`, `decision.md`, and `task.md` there.

For each one:
- Check whether the chosen project already has a schema for that type
  (`search_notes` with `metadata_filters={"type": "schema"}`, or try
  `read_note("schemas/<name>")`). **If it exists, skip it** — never overwrite a
  schema the user may have customized.
- Otherwise write it with `write_note`, routed to `primaryProject` (pass it as
  `project`, or as `project_id` if it's an `external_id` UUID):
  - `directory="schemas"`, `note_type="schema"`, `title` = the schema's title
    (Session / Decision / Task).
  - `content` = the markdown **body only** — everything *after* the `---`
    frontmatter block (the `# Session` heading and the prose).
  - `metadata` = the schema's structured frontmatter as a **nested dict**: `entity`,
    `version`, the full `schema` map, and `settings` (keep its nested `frontmatter`,
    and pass enum values as JSON arrays, e.g. `["open","resumed","closed"]`).
  - **Do not** put the schema's `---` frontmatter inside `content`. On the cloud
    write path that nested YAML is silently coerced to the string `'[object Object]'`
    (basic-memory-cloud#1000), corrupting `schema`/`settings`. The `metadata` param
    round-trips correctly on both local and cloud. After seeding, verify one note
    with `read_note(..., output_format="json", include_frontmatter=true)` —
    `schema`/`settings` must come back as nested objects, not strings.

### 2. Install the shared skills (if the user opted in)
**First, guard against clobbering a source checkout.** If `./skills` already exists,
is tracked in git, and holds `memory-*` directories, you're inside the skills' own
source repo (e.g. `basic-memory` itself) — the install would overwrite the working
copy with published versions. In that case **skip the install** and tell the user
the skills are already present as source; don't run the command. Quick check:

```
git ls-files skills/ | grep -q memory- && echo "source repo - skip install"
```

Otherwise, run from the project root:

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
    "placementConventions": "<learned or suggested summary, or null>",
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

### 4. Smoke-test the wiring
Before you close, prove recall actually resolves — this catches a misnamed project,
missing cloud credentials, or a ref that doesn't route, while the user is still here
to fix it. Run the same structured query the SessionStart hook runs, via the CLI it
uses (`basic-memory` / `bm` / `uvx basic-memory`):

- **Primary:** `… tool search-notes --type schema --page-size 5` against
  `primaryProject` — use `--project-id <uuid>` for a UUID, `--project <ref>`
  otherwise. It should return the three schemas you just seeded.
- **One shared project** (only if `secondaryProjects` is non-empty): a
  `--type decision --status open` query against the first ref. It just needs to
  return *cleanly* — `0 results` is fine; an **error** means the ref doesn't route.

If a query errors, or the primary returns nothing, surface it and fix the project
ref before closing — don't let the next session's brief come up empty.

## Close

Confirm what you did in a few lines: the project mapping, which schemas were seeded
vs. already present, whether placement was learned or suggested, the smoke-test
result, and whether the output style is on.

Then handle activation based on the output style:
- **Output style enabled** → it's fixed at session start, so the full capture
  reflexes only take effect next session. Prompt the user to **restart the session**
  (start a new Claude Code session) to activate them. Be precise so it doesn't read
  as "nothing works yet": recall is already live this session (the SessionStart
  hook's prompt ran), and the PreCompact checkpoint works now too — only the
  proactive-capture reflexes wait for the restart.
- **Output style off** → no restart needed; the hooks already run.

End with: *"Done — I'll use this from the next message. Run `/basic-memory:bm-status`
anytime to see what I'm tracking."*
