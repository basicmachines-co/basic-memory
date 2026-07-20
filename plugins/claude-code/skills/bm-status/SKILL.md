---
name: bm-status
description: Show the Basic Memory plugin's current state for this project — active project, capture folders, output style, recent session checkpoints, and whether Basic Memory is reachable.
disable-model-invocation: true
---

# Basic Memory status

Report the plugin's current state for this project, then present a concise summary.
This is a quick diagnostic — gather the facts and lay them out; don't over-investigate.

## Gather

1. **CLI reachable?** Run `basic-memory --version`, then `bm --version`, then
   `uvx basic-memory --version`. If no launcher resolves, report the CLI status as
   unavailable and continue with MCP/config checks. The plugin hooks can still use
   their uv-managed environment.

2. **Configuration.** Read the `basicMemory` block with the hooks' precedence —
   user-level `~/.claude/settings.json` as the base, then the project's
   `.claude/settings.json` and `.claude/settings.local.json` overriding per key —
   and report (note when a value comes from the user-level block vs. the project):
   - From the `basicMemory` block: `primaryProject` (or note none is pinned — the
     default project is used), `secondaryProjects` (team/shared read sources),
     `teamProjects` (share targets for `/basic-memory:bm-share`), `captureFolder`
     (default `sessions`), `rememberFolder` (default `bm-remember`), and
     `preCompactCapture` mode (default `extractive`), `captureEvents` (default
     `false`), `redactKeys`, and `redactPaths`.
   - From the **root** settings object (not `basicMemory`): whether `outputStyle` is
     `basic-memory` — i.e. whether the capture reflexes are on.

3. **Core hook health.** With the first available launcher, run
   `basic-memory hook status --harness claude --project-dir <project-root>`.
   Report its shared inbox path, pending envelopes, processed envelopes, last
   flush, settings state, resolved primary project, capture state, capture folder,
   Basic Memory version, and uv version. Inbox counts are global across supported
   harnesses; do not attribute a backlog solely to Claude. Treat this command's
   settings resolution as canonical for hook behavior; if it disagrees with the
   manually merged config, show the mismatch. If no launcher resolves, mark these
   fields unavailable and continue.

4. **Recent checkpoints.** `search_notes` with
   `metadata_filters={"type": "session"}`, `page_size` 5, scoped to `primaryProject`
   if one is set. List the most recent session checkpoints by title + permalink.

5. **Active tasks.** `search_notes` with
   `metadata_filters={"type": "task", "status": "active"}` — report just the count.

When scoping these queries to `primaryProject`, pass it as `project`, or as
`project_id` if it's an `external_id` UUID (a bare UUID in `project` won't route).

## Present

Lay it out like this (fill in real values; write "—" or a short note for anything
you couldn't determine, rather than failing the whole report):

```
## Basic Memory status

- CLI:               basic-memory <version>
- Project:           <primaryProject, or "default project (not pinned)">
- Reads from (team): <secondaryProjects joined, or "none">
- Share targets:     <teamProjects keys joined, or "none">
- Capture folder:    <captureFolder>
- Remember folder:   <rememberFolder>
- Output style:      <enabled | not enabled>
- PreCompact:        <mode>
- Event capture:     <enabled | disabled>
- Redact keys:       <configured count or none>
- Redact paths:      <configured count or none>
- Shared hook inbox: <path or unavailable>
- Shared pending envelopes: <count or unavailable>
- Shared processed envelopes: <count or unavailable>
- Last flush:        <timestamp, never, or unavailable>
- Hook runtime:      basic-memory <version>; uv <version or missing>
- Recent checkpoints: <n>
    - <title> — <permalink>
    ...
- Active tasks:      <n>
```

If there are no checkpoints yet, say "none yet" and remind the user that checkpoints
are written automatically before context compaction (and that a `primaryProject` must
be set for them to be written).

Warn when event capture is enabled and pending envelopes are accumulating or the
last flush is `never`, while noting that another harness may contribute to the
shared counts. Do not warn about an empty inbox when capture is disabled.
