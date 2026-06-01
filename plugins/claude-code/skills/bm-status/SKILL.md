---
name: bm-status
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
   if present) and report:
   - From the `basicMemory` block: `primaryProject` (or note none is pinned — the
     default project is used), `secondaryProjects` (team/shared read sources),
     `teamProjects` (share targets for `/basic-memory:bm-share`), `captureFolder`
     (default `sessions`), `rememberFolder` (default `bm-remember`), and
     `preCompactCapture` mode (default `extractive`).
   - From the **root** settings object (not `basicMemory`): whether `outputStyle` is
     `basic-memory` — i.e. whether the capture reflexes are on.

3. **Recent checkpoints.** `search_notes` with
   `metadata_filters={"type": "session"}`, `page_size` 5, scoped to `primaryProject`
   if one is set. List the most recent session checkpoints by title + permalink.

4. **Active tasks.** `search_notes` with
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
- Recent checkpoints: <n>
    - <title> — <permalink>
    ...
- Active tasks:      <n>
```

If there are no checkpoints yet, say "none yet" and remind the user that checkpoints
are written automatically before context compaction (and that a `primaryProject` must
be set for them to be written).
