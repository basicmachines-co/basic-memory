---
title: bm-agent-get(3)
type: manpage
section: 3
name: bm-agent-get
summary: read one Basic Memory agent run, or the diff of each note it changed (hosted server only)
generated: hand
tool: bm_agent_get
---

# bm-agent-get(3)

## NAME

**bm-agent-get** — read one Basic Memory agent run, or the diff of each note it changed (hosted server only)

## SYNOPSIS

```
bm_agent_get(run_id, changes=False, project=None, output_format="text")
```

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server, for workspaces whose plan includes agents. Read-only.

Without `changes`: the run's status, why it is still waiting, the notes it
changed, and the agent's last reply.

With `changes=True`: each note the run changed, as a unified diff, to read
before keeping the run with bm_agent_mark(3).

`run_id` is the id bm_agent_run(3) or bm_agent_runs(3) returned.

## MCP USAGE

```
bm_agent_get(run_id="<run id>", project="work")
bm_agent_get(run_id="<run id>", changes=True, project="work")
```

## SEE ALSO

- see_also [[agents(7)]]
- see_also [[bm-agent-runs(3)]]
- see_also [[bm-agent-mark(3)]]
