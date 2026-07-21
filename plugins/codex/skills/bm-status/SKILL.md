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
   - fallback `uvx basic-memory --version`

   Keep going if no launcher resolves. The plugin hook scripts can still use
   their uv-managed environment; report hook health as unavailable instead of
   claiming the hooks cannot work.

2. Plugin config:
   - read `.codex/basic-memory.json`
   - report `primaryProject`, `secondaryProjects`, `teamProjects`,
     `captureFolder`, `rememberFolder`, `recallTimeframe`, `focus`,
     `captureEvents`, `redactKeys`, and `redactPaths`

3. Core hook health:
   - with the first available launcher, run
     `basic-memory hook status --harness codex --project-dir <repo-root>`
   - report the shared inbox path, pending envelopes, processed envelopes, last
     flush, settings state, resolved primary project, capture state, capture
     folder, Basic Memory version, and uv version from that command
   - inbox counts are global across supported harnesses; do not attribute a
     backlog solely to Codex
   - treat the command's settings resolution as canonical for hook behavior; if
     it disagrees with the manually read config, show the mismatch

4. Hook files:
   - confirm `plugins/codex/hooks/hooks.json` exists if running from this repo
   - remind the user that Codex plugin hooks must be reviewed and trusted before
     they run

5. Basic Memory queries:
   - query recent `type=codex_session` and `type=session`, then merge, deduplicate,
     sort newest first, and keep the newest five; the first type covers deliberate
     and PreCompact Codex checkpoints, while the second covers core projections
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
- Event capture: <enabled | disabled>
- Redact keys: <configured count or none>
- Redact paths: <configured count or none>
- Shared hook inbox: <path or unavailable>
- Shared pending envelopes: <count or unavailable>
- Shared processed envelopes: <count or unavailable>
- Last flush: <timestamp, never, or unavailable>
- Hook runtime: basic-memory <version>; uv <version or missing>
- Recent checkpoints: <count across codex_session and session>
- Active tasks: <count>
- Open decisions: <count>
- Hooks: installed; trust review required in Codex
```

List recent checkpoints by type, title, and permalink when available. Warn when
event capture is enabled and pending envelopes are accumulating or the last flush
is `never`, while noting that another harness may contribute to the shared counts.
Do not warn about an empty inbox when capture is disabled.
