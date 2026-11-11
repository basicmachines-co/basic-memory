---
title: agents(7)
type: manpage
section: 7
name: agents
summary: Basic Memory Cloud agents - agent notes, what starts a run, and how runs are reviewed
generated: hand
---

# agents(7)

## NAME

**agents** — Basic Memory Cloud agents: agent notes, what starts a run, and how runs are reviewed

## DESCRIPTION

Agents run only on the hosted Basic Memory Cloud MCP server, for workspaces
whose plan includes agents. A local server registers no agent tools.

An agent is a note with `type: agent` in its frontmatter. Its body is the
instructions, written and changed with bm_write(3) and bm_edit(3). Cloud runs
it on its own machine, with the note tools, in the agent note's project only.
Five tools do everything else:

| Tool | Does |
| --- | --- |
| [[bm-agent-list(3)]] | The project's agents, or one agent's status |
| [[bm-agent-run(3)]] | Start an agent; returns a run id |
| [[bm-agent-get(3)]] | One run, or with `changes`, the diff of each note it changed |
| [[bm-agent-runs(3)]] | Recent runs, or with `whole_workspace`, the review queue |
| [[bm-agent-mark(3)]] | Mark a run, or every waiting run, `kept` or `dismissed` |

Undo is not a tool. It puts notes back, so it stays the person's click on
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

Without a watch or a schedule, the agent runs only when someone starts it
with bm_agent_run(3), on nothing, a note or a folder.
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

## GOTCHAS

- A scheduled run is skipped, for free, when no note in the project changed
  since the agent's last run. A run started with bm_agent_run(3) is never
  skipped that way.
- Once the workspace reaches its spending limit, bm_agent_run(3) is refused
  and watches and schedules start nothing until the limit is raised.

## SEE ALSO

- see_also [[bm-agent-list(3)]]
- see_also [[bm-agent-run(3)]]
- see_also [[bm-agent-get(3)]]
- see_also [[bm-agent-runs(3)]]
- see_also [[bm-agent-mark(3)]]
- see_also [[bm-write(3)]]
