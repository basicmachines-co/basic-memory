---
title: bm-agent-runs(3)
type: manpage
section: 3
name: bm-agent-runs
summary: list recent Basic Memory agent runs, or every run in the workspace waiting for review (hosted server only)
generated: hand
tool: bm_agent_runs
---

# bm-agent-runs(3)

## NAME

**bm-agent-runs** — list recent Basic Memory agent runs, or every run in the workspace waiting for review (hosted server only)

## SYNOPSIS

```
bm_agent_runs(project=None, agent=None, since=None, needs_review=False,
              whole_workspace=False, output_format="text")
```

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server, for workspaces whose plan includes agents. Read-only.

Recent runs in `project`, newest first. `agent` filters to one agent, `since`
sets the window (14 days by default, at most 90), and `needs_review` keeps
only runs nobody has kept, undone or dismissed. `project` is required here.

With `whole_workspace=True`: every run in the workspace waiting for review,
across projects, with a link to each run's page, and who reviewed the latest
runs. `agent`, `since` and `needs_review` do not apply, and `project` is
optional; it only picks the workspace.

## MCP USAGE

```
bm_agent_runs(needs_review=True, project="basic-memory-llc")
# → # Agent runs in Basic Memory LLC, last 14 days
#   - 2026-10-28 12:06 UTC · Log compactor · Failed: the agent did not finish
#     before its deadline, 0 notes changed · needs review · run 3ee8452f-...
#   - 2026-10-28 11:49 UTC · Agent briefing · Completed, 1 note changed
#     · needs review · run 78b55520-...

bm_agent_runs(whole_workspace=True)
```

## SEE ALSO

- see_also [[agents(7)]]
- see_also [[bm-agent-get(3)]]
- see_also [[bm-agent-mark(3)]]
