---
title: bm-agent-run(3)
type: manpage
section: 3
name: bm-agent-run
summary: start a Basic Memory agent on a note, a folder, or nothing, and return the run id (hosted server only)
generated: hand
tool: bm_agent_run
---

# bm-agent-run(3)

## NAME

**bm-agent-run** — start a Basic Memory agent on a note, a folder, or nothing, and return the run id (hosted server only)

## SYNOPSIS

```
bm_agent_run(agent, project, identifier=None, output_format="text")
```

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server, for workspaces whose plan includes agents. Destructive: the run may
change notes in the project.

Starts the agent note `agent` (title, path or id) and returns a run id. What
it works on depends on `identifier`:

- none: the agent follows its instructions and finds its own work, as a
  scheduled run does.
- a note (title, permalink or path): the agent reads that note first.
- a folder, ending in `/` (`meetings/`): the agent works on that folder.
  The folder is relative to the project; `..` and absolute paths are
  refused.

`project` is required. agents(7) describes agent notes and review.

## WHAT A RUN CAN DO

- An agent is a note in the user's project. Its instructions are ones the
  user wrote or picked from a ready-made template.
- A run is confined to that one project. It runs only if both the person who
  last saved the agent note and the person who starts the run can edit the
  project.
- It may create, edit, move or delete notes in that project, and nothing else.
- It reads the web only when the agent note asks for it and a workspace owner
  or admin has allowed agents to read the web.
- Each run that finishes uses the workspace's credits. Failed and skipped runs
  use none.
- Every change can be reviewed: read the diffs with
  `bm_agent_get(run_id, changes=True)`, keep the run with bm_agent_mark(3), or
  undo it on the run's page in the app.

## MCP USAGE

```
bm_agent_run(agent="Curator", identifier="projects/Launch", project="work")
# → the run id; follow it with bm_agent_get
```

## GOTCHAS

- The call returns at once with the run queued. Agents usually take one to a
  few minutes; follow the run with bm_agent_get(3).
- Starting an agent again on the same thing (the same note, the same folder,
  or nothing) while a run for it waits to start joins that run instead of
  queuing a second one.
- Once the workspace reaches its spending limit, the call is refused.

## SEE ALSO

- see_also [[agents(7)]]
- see_also [[bm-agent-get(3)]]
- see_also [[bm-agent-list(3)]]
