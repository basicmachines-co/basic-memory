# Getting started

A ~5-minute walkthrough from zero to a working memory bridge. New to the idea? Read
[why-combine-memory.md](./why-combine-memory.md) first.

## 1. Prerequisites

- **Claude Code.**
- **Basic Memory** (`>= 0.19.0`) installed and connected as an MCP server:
  ```bash
  uv tool install basic-memory
  ```
  Then add it to Claude Code (`claude mcp add` or your MCP config). Confirm it's
  reachable — Claude should be able to call `list_memory_projects`.

You don't need a Basic Memory Cloud account. Everything works local-first; cloud and
teams are optional (see step 5).

## 2. Install the plugin

```bash
claude plugin marketplace add basicmachines-co/basic-memory --sparse .claude-plugin plugins/claude-code
claude plugin install basic-memory@basicmachines-co
```

Verify it loaded:

```bash
claude plugin details basic-memory@basicmachines-co
```

You should see **Skills (4): remember, setup, share, status** and **Hooks (2):
SessionStart, PreCompact**.

## 3. Run setup

In a project (repo) where you want memory, run:

```
/basic-memory:bm-setup
```

It's a short interview. It will:
- map this project to a Basic Memory project (pick an existing one or create a new one),
- seed the `session` / `decision` / `task` schemas so notes are findable by structured
  search,
- install the shared `memory-*` skills (`npx skills add …`) — the plugin ships only the
  Claude-Code-specific glue and pulls the canonical skills on demand,
- optionally learn your existing folder/naming conventions,
- enable the capture reflexes (output style),
- write a `basicMemory` block to `.claude/settings.json`.

> No cloud, no interview yet? The plugin still works against your **default** Basic
> Memory project with zero config — but pinning a project (which setup does) is what
> lets the PreCompact checkpoint write, and stops the first-run nudge.

When it finishes, run:

```
/basic-memory:bm-status
```

to see exactly what the plugin is tracking.

## 4. See it work

1. **Capture a decision.** In normal conversation, make a decision — e.g. *"Let's use
   Postgres, not SQLite, because we need concurrent writers."* With the output style on,
   Claude writes a `type: decision` note and tells you the permalink.
2. **Quick-capture something.** `/basic-memory:bm-remember switch the staging job to the
   new image after the rebase lands` → saved to `bm-remember/`.
3. **Start a fresh session.** Open a new Claude Code session in the same project. The
   **SessionStart brief** appears first thing, showing your active tasks and the open
   decision you just captured — Claude is oriented before you type anything.
4. **(Optional) Trigger compaction** on a long session and resume: the next session's
   brief includes the checkpoint the PreCompact hook wrote.

That loop — capture → checkpoint → brief — is the whole point. It gets richer as the
graph accumulates.

## 5. Add your team (optional)

On Basic Memory Cloud with a team workspace, you can read team context into your brief
and publish back deliberately.

Re-run `/basic-memory:bm-setup` (or edit `.claude/settings.json`). Because project names
repeat across workspaces, team projects use **workspace-qualified names**
(`my-team/notes`) or `external_id` UUIDs — setup finds these for you via
`list_workspaces`.

```json
{
  "basicMemory": {
    "primaryProject": "my-org/main",
    "secondaryProjects": ["my-team/main", "my-team/notes"],
    "teamProjects": { "my-team/notes": { "promoteFolder": "shared" } }
  },
  "outputStyle": "basic-memory"
}
```

Now:
- SessionStart folds the team's **open decisions** into your brief (read-only).
- Your captures still go **only** to `primaryProject` — never to the team.
- `/basic-memory:bm-share <note>` publishes a chosen note to `my-team/notes/shared`, with
  attribution and a confirmation step.

Tip: a team brief is only as rich as the team's typed notes. Share an existing decision
into a team project and watch it appear in the next session's brief.

## 6. Tune it (optional)

Everything is in the `basicMemory` block of `.claude/settings.json`. Common knobs:

| Key | Default | What it does |
|-----|---------|--------------|
| `primaryProject` | (default project) | where briefs read from and captures write to |
| `secondaryProjects` | `[]` | team/shared projects read for recall (read-only) |
| `teamProjects` | `{}` | share targets for `/basic-memory:bm-share` |
| `captureFolder` | `sessions` | folder for PreCompact checkpoints |
| `rememberFolder` | `bm-remember` | folder for `/basic-memory:bm-remember` |
| `recallTimeframe` | `3d` | recency window for the brief |
| `preCompactCapture` | `extractive` | how checkpoints are produced |

See [settings.example.json](../settings.example.json) for the full shape.

## Troubleshooting

- **No brief at session start?** Confirm Basic Memory is connected (`/basic-memory:bm-status`).
  The hooks are silent if `basic-memory` isn't on PATH.
- **Checkpoints aren't being written?** A `primaryProject` must be set — the PreCompact
  hook never writes to an un-pinned/default project on its own.
- **Commands not showing?** They're namespaced: type `/basic-memory:` to see them.
- **Team brief is empty?** Those projects may have no `type: decision` notes yet — the
  brief surfaces typed decisions, which accumulate as the team captures them.

For the design and internals, see [architecture.md](./architecture.md) and
[DESIGN.md](../DESIGN.md).
