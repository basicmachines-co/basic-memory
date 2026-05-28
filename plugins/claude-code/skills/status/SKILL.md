---
name: status
description: Show the Basic Memory plugin's current state for this project — active project, capture folders, output style, recent session checkpoints, and whether Basic Memory is reachable.
disable-model-invocation: true
---

# Basic Memory status

Report the plugin's current state for this project, then present a concise summary.
This is a quick diagnostic — gather the facts and lay them out; don't over-investigate.

## Gather

1. **CLI reachable?** Run `basic-memory --version` (fall back to `bm --version`). If
   neither is found, report that Basic Memory isn't installed or on PATH, and stop —
   nothing else will work without it.

2. **Configuration.** Read `.claude/settings.json` (and `.claude/settings.local.json`
   if present) and report from the `basicMemory` block:
   - `primaryProject` — or note that none is pinned (the default project is used)
   - `secondaryProjects` — team/shared projects read for recall (read-only), if any
   - `teamProjects` — share targets for `/basic-memory:share`, if any
   - `captureFolder` (default `sessions`) and `rememberFolder` (default `bm-remember`)
   - whether `outputStyle` is `basic-memory` (capture reflexes on/off)
   - `preCompactCapture` mode (default `extractive`)

3. **Recent checkpoints.** `search_notes` with
   `metadata_filters={"type": "session"}`, `page_size` 5, scoped to `primaryProject`
   if one is set. List the most recent session checkpoints by title + permalink.

4. **Active tasks.** `search_notes` with
   `metadata_filters={"type": "task", "status": "active"}` — report just the count.

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
- Recent checkpoints: <n>
    - <title> — <permalink>
    ...
- Active tasks:      <n>
```

If there are no checkpoints yet, say "none yet" and remind the user that checkpoints
are written automatically before context compaction (and that a `primaryProject` must
be set for them to be written).
