---
name: bm-status
description: Report the Basic Memory for Codex configuration, reachability, hook expectations, recent Codex checkpoints, and active tasks.
---

# Basic Memory For Codex Status

Gather a concise diagnostic. Do not over-investigate.

## Gather

1. CLI reachability:
   - `basic-memory --version`
   - fallback `bm --version`

2. Plugin config:
   - read `.codex/basic-memory.json`
   - report `primaryProject`, `secondaryProjects`, `teamProjects`,
     `captureFolder`, `rememberFolder`, `recallTimeframe`, and `focus`

3. Hook files:
   - confirm `plugins/codex/hooks/hooks.json` exists if running from this repo
   - remind the user that Codex plugin hooks must be reviewed and trusted before
     they run

4. Basic Memory queries:
   - recent `type=codex_session`, page size 5
   - active `type=task`, `status=active`
   - open `type=decision`, `status=open`

## Present

Use this shape:

```text
Basic Memory for Codex
- CLI: <version or missing>
- Project: <primaryProject or default>
- Reads from: <secondaryProjects or none>
- Share targets: <teamProjects or none>
- Capture folder: <captureFolder>
- Remember folder: <rememberFolder>
- Recall timeframe: <recallTimeframe>
- Recent Codex checkpoints: <count>
- Active tasks: <count>
- Open decisions: <count>
- Hooks: installed; trust review required in Codex
```

List recent checkpoints by title and permalink when available.
