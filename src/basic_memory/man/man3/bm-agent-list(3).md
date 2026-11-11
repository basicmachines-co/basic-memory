---
title: bm-agent-list(3)
type: manpage
section: 3
name: bm-agent-list
summary: list a project's Basic Memory agents, or show whether one agent can run (hosted server only)
generated: hand
tool: bm_agent_list
---

# bm-agent-list(3)

## NAME

**bm-agent-list** — list a project's Basic Memory agents, or show whether one agent can run (hosted server only)

## SYNOPSIS

```
bm_agent_list(project, agent=None, output_format="text")
```

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server, for workspaces whose plan includes agents. Read-only.

Without `agent`: the project's agents, what starts each one, and how its
recent runs went.

With `agent` (title, path or id of the agent note): whether the agent can run
and, if not, Cloud's reason (an invalid note, turned off, the spending limit);
what starts it; whether it reads the web; and the model it runs on.

`project` is required: a project name, `workspace/project`, or external id,
as for the note tools. agents(7) describes agent notes.

## MCP USAGE

```
bm_agent_list(project="basic-memory-llc")
# → # Agents in Basic Memory LLC
#   - Agent briefing (agents/Agent briefing.md): runs daily at 08:00 UTC,
#     next 2026-10-29 08:00 UTC; 1 runs, last run completed at 2026-10-28 11:49 UTC
#   - Curator (agents/Curator.md): 0 runs, no runs in 14 days
#   ...

bm_agent_list(agent="Curator", project="basic-memory-llc")
```

## SEE ALSO

- see_also [[agents(7)]]
- see_also [[bm-agent-run(3)]]
- see_also [[bm-agent-runs(3)]]
