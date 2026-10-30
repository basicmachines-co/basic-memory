---
title: bm-log(3)
type: manpage
section: 3
name: bm-log
summary: read the workspace log of who changed which notes, folders and projects (hosted server only)
generated: hand
tool: bm_log
---

# bm-log(3)

## NAME

**bm-log** — read the workspace log of who changed which notes, folders and projects (hosted server only)

## SYNOPSIS

```
bm_log(since=None, event_types=(), cursor=None, limit=50, full=False,
       project=None)
```

## PARAMETERS

- **since** (string | null, optional, default: None) — Events at or after this point: an RFC 3339 instant with a timezone (`2026-07-22T15:30:00Z`) is exact; a date (`2026-07-22`) starts at midnight UTC; `7d` or `2 days ago` look back whole days. Also accepted as `after_date` or `timeframe`.
- **event_types** (array, optional, default: ()) — Event types to include; omit or pass an empty array for all. See DESCRIPTION for the list.
- **cursor** (string | null, optional, default: None) — The `nextCursor` of the previous page, to resume.
- **limit** (integer, optional, default: 50) — Events per page (1-100).
- **full** (boolean, optional, default: False) — Return every field of each event: workspace, project, note and actor ids, versions, checksums and app links.
- **project** (string | null, optional, default: None) — Limit the log to one project: name, `workspace/project`, or external id. Omit for the whole workspace.

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server. A local client will not find this tool.

`bm_log` reads the workspace log: who changed which notes (created, updated,
moved, deleted), folder and project changes, indexing, imports and agent
runs, with who did each when known. It is read-only. Events come newest
first, as metadata only. The reply carries an opaque `nextCursor`; pass it
back as `cursor` to read the next page.

Each event holds only the fields that say what happened. `full=True` returns
every field, as the web app's activity API does.

`event_types` takes any of: `note.created`, `note.updated`, `note.deleted`,
`note.moved`, `folder.moved`, `folder.deleted`, `project.created`,
`project.deleted`, `index.completed`, `user.preferences.updated`,
`operation.import`, `operation.project_index`, `operation.snapshot_export`,
`operation.tenant_provision`, `operation.tenant_deprovision` and
`operation.agent_run`.

`bm_log` does not return note content. To read a note, use bm_read(3). To
read recently changed notes' content, use `bm_project(command="recent",
project="<project>")`, see bm-project(3).

## EXAMPLES

```
bm_log(since="7d", event_types=["note.deleted", "note.moved"])
bm_log(project="main", limit=20)
bm_log(cursor="<nextCursor from the previous page>")
bm_log(since="2026-07-22", full=True)
```

## GOTCHAS

- The log is an event stream, not a list of current notes. A note that was
  created and then deleted appears as two events.
- `since` with `7d` or `2 days ago` looks back whole days, not hours.
- `project` narrows the log to one project; without it the log covers the
  whole workspace.
- Pass `cursor` unchanged. It is opaque and tied to the filters of the call
  that returned it.

## SEE ALSO

- see_also [[bm-read(3)]]
- see_also [[bm-project(3)]]
- see_also [[bm-man(3)]]
