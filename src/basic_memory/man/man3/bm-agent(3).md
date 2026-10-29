---
title: bm-agent(3)
type: manpage
section: 3
name: bm-agent
summary: run Basic Memory agents, follow their runs, and review what they changed (hosted server only)
generated: hand
tool: bm_agent
verified: 0.24.1 mcp
---

# bm-agent(3)

## NAME

**bm-agent** — run Basic Memory agents, follow their runs, and review what they changed (hosted server only)

## SYNOPSIS

```
bm_agent(command, project, agent=None, identifier=None, run_id=None,
         decision=None, since=None, needs_review=False, output_format="text")
```

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server, for workspaces whose plan includes agents. A local client will not
find this tool.

An agent is a note with `type: agent` in its frontmatter. Its body is the
instructions, written and changed with bm_write(3) and bm_edit(3). Cloud runs
it on its own machine, with the note tools, in the agent note's project only.
`bm_agent` does everything else, one `command` at a time:

- **list**: the project's agents, what starts each one, and how its recent
  runs went.
- **status** (`agent`): whether the agent can run and, if not, Cloud's
  reason (an invalid note, turned off, the spending limit); what starts it;
  whether it reads the web; and the model it runs on.
- **run** (`agent`, `identifier`): start the agent on a note. It may change
  notes in the project, and returns a run id. A scheduled agent needs no
  `identifier`: it runs on its own note, as its schedule does.
- **get** (`run_id`): one run: its status, why it is still waiting, the notes
  it changed, and the agent's last reply.
- **changes** (`run_id`): each note the run changed, as a unified diff, to
  read before keeping the run.
- **runs**: recent runs, newest first. `agent` filters to one agent, `since`
  sets the window (14 days by default, at most 90), and `needs_review` keeps
  only runs nobody has kept, undone or dismissed.
- **review**: every run in the workspace waiting for review, across projects,
  with a link to each run's page, and who reviewed the latest runs.
- **keep_all**: keep every finished run with changes that waits for review,
  in projects the caller can edit. The reply names the runs Cloud refused.
- **mark** (`run_id`, `decision`): record a finished run as `kept` or
  `dismissed`, for the whole workspace. It changes no notes.

Every command needs `project`: a project name, `workspace/project`, or
external id, as for the note tools. For **review** and **keep_all** it only
picks the workspace.

Undo is not a command. It puts notes back, so it stays the person's click on
the run's page, where they can see what goes back.

## AGENT NOTES

The frontmatter Cloud reads from an agent note:

- **type**: `agent`.
- **title**: the agent's name, at most 200 characters.
- **enabled**: `true` to let it run. `yes`, `on` and `1` work too.
- **watch** (optional): start the agent on each new note under these paths,
  relative to the project, with no leading `/`. A folder (`meetings/`) or a
  pattern (`meetings/staff-*.md`, `*` any characters, `?` one); up to 20.
- **schedule** (optional): run the agent on its own note at these times, at
  most once an hour. In words (`daily at 9:00`, `weekdays at 9am`,
  `mondays at 8:00`), as an alias (`@daily`), or as 5-field cron. Quote it.
- **timezone** (optional): the zone the schedule's times are in, like
  `America/Chicago`. UTC when left out.
- **web** (optional): `true` to search the web and read pages, or
  `{allow: [example.com]}` to keep it to those domains. It works only when the
  workspace allows agents to read the web.

Without a watch or a schedule, the agent runs only when someone starts it.
The ready-made agents from the app's **Add an agent** dialog are MCP
resources: `memory://agents/templates` lists them, and
`memory://agents/templates/<id>` is one whole agent note, ready to save with
bm_write(3).

## REVIEW RULES

- A finished run that changed notes waits for review until someone keeps or
  undoes it. A failed or skipped run waits until someone dismisses it. A
  completed run that changed nothing needs no review.
- Reviewing a run needs edit access to its project.
- A review is shared: one person keeping a run keeps it for the workspace.
- Editing a note the run changed keeps the run.

## MCP USAGE

Verified:

```
bm_agent(command="list", project="basic-memory-llc")
# → # Agents in Basic Memory LLC
#   - Agent briefing (agents/Agent briefing.md): runs daily at 08:00 UTC,
#     next 2026-10-29 08:00 UTC; 1 runs, last run completed at 2026-10-28 11:49 UTC
#   - Curator (agents/Curator.md): 0 runs, no runs in 14 days
#   ...

bm_agent(command="runs", needs_review=True, project="basic-memory-llc")
# → # Agent runs in Basic Memory LLC, last 14 days
#   - 2026-10-28 12:06 UTC · Log compactor · Failed: the agent did not finish
#     before its deadline, 0 notes changed · needs review · run 3ee8452f-...
#   - 2026-10-28 11:49 UTC · Agent briefing · Completed, 1 note changed
#     · needs review · run 78b55520-...
```

## GOTCHAS

- `run` returns at once with the run queued. Agents usually take one to a few
  minutes; follow the run with `get`.
- A scheduled run is skipped, for free, when no note in the project changed
  since the agent's last run. A run started with `run` is never skipped that
  way.
- Once the workspace reaches its spending limit, `run` is refused and watches
  and schedules start nothing until the limit is raised.

## SEE ALSO

- see_also [[bm-write(3)]]
- see_also [[bm-edit(3)]]
- see_also [[bm-find(3)]]
- see_also [[bm-man(3)]]
