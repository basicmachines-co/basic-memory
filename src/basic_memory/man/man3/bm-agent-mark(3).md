---
title: bm-agent-mark(3)
type: manpage
section: 3
name: bm-agent-mark
summary: mark a Basic Memory agent run, or every run waiting for review, kept or dismissed (hosted server only)
generated: hand
tool: bm_agent_mark
---

# bm-agent-mark(3)

## NAME

**bm-agent-mark** — mark a Basic Memory agent run, or every run waiting for review, kept or dismissed (hosted server only)

## SYNOPSIS

```
bm_agent_mark(decision, run_id=None, project=None, output_format="text")
```

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server, for workspaces whose plan includes agents. It records a review for
the whole workspace and changes no notes.

With `run_id`: record that finished run as `kept` (its changes are right) or
`dismissed` (acknowledge a failed or skipped run).

Without `run_id` and with `decision="kept"`: keep every finished run with
changes that waits for review, in projects the caller can edit. The reply
names the runs Cloud refused. `decision="dismissed"` needs a `run_id`.

To undo a run, the person uses Undo on the run's page. agents(7) gives the
review rules.

## MCP USAGE

```
bm_agent_mark(decision="kept", run_id="<run id>", project="work")
bm_agent_mark(decision="kept")
```

## SEE ALSO

- see_also [[agents(7)]]
- see_also [[bm-agent-get(3)]]
- see_also [[bm-agent-runs(3)]]
