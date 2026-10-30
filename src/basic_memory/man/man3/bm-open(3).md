---
title: bm-open(3)
type: manpage
section: 3
name: bm-open
summary: open the Basic Memory app at a notes, folders, note, search or graph view (hosted server only)
generated: hand
tool: bm_open
---

# bm-open(3)

## NAME

**bm-open** — open the Basic Memory app at a notes, folders, note, search or graph view (hosted server only)

## SYNOPSIS

```
bm_open(view="notes", path=None, identifier=None, query=None,
        graph_mode="overview", note_mode="view", project=None)
```

## PARAMETERS

- **view** (`notes` | `note` | `folders` | `search` | `graph`, optional, default: "notes") — Destination screen. Use `note` with `identifier`, `folders` with `path`, `search` with `query`, or `graph` with `graph_mode`.
- **path** (string | null, optional, default: None) — Initial folder for the `notes`, `folders`, `note` or `search` views. Omit for the project root. Also accepted as `directory`. Not valid with `graph`.
- **identifier** (string | null, optional, default: None) — Note title, permalink, `memory://` URL, or exact note external UUID. Required with `view="note"`; optional with `view="graph"` to focus that note; omit for other views.
- **query** (string | null, optional, default: None) — Initial search query. Only valid with `view="search"`; omit to open a blank search.
- **graph_mode** (`overview` | `full`, optional, default: "overview") — Graph display mode. Only valid with `view="graph"`.
- **note_mode** (`view` | `edit`, optional, default: "view") — Initial note mode. `edit` opens the note editor directly. Only valid with `view="note"`.
- **project** (string | null, optional, default: None) — Project name, `workspace/project`, or project external id. Omit to use the default. Also accepted as `project_id`.

## DESCRIPTION

**Availability:** registered only on the hosted Basic Memory Cloud MCP
server, and shown to clients that can display the Basic Memory app (MCP Apps
clients and ChatGPT). A local client will not find this tool. It is not
offered to Cloud agent runs.

`bm_open` shows the person the Basic Memory app: the notes gallery, a folder,
a note, search, or the graph. It is read-only. Use it when the person wants to
open, browse or interact with their notes visually. Do not use it only to get
information for your own answer; use the data tools for that: bm_read(3),
bm_search(3), bm_list(3).

The default workspace and project resolve on their own, so do not list
projects first.

Some parameters belong to one view, and the call is refused when they are
combined with another:

- `identifier` only with `view="note"` or `view="graph"`.
- `query` only with `view="search"`.
- `path` not with `view="graph"`.
- `graph_mode` other than `overview` only with `view="graph"`.
- `note_mode` other than `view` only with `view="note"`.

## EXAMPLES

```
bm_open()
bm_open(view="search", query="launch plan")
bm_open(view="note", identifier="plans/launch-plan", note_mode="edit")
bm_open(view="folders", path="meetings")
bm_open(view="graph", graph_mode="full")
```

## GOTCHAS

- `view="note"` needs `identifier`; without it there is no note to show.
- It opens a view for the person. The reply is not the note's content.
- Clients that cannot display the app do not get this tool.

## SEE ALSO

- see_also [[bm-read(3)]]
- see_also [[bm-search(3)]]
- see_also [[bm-list(3)]]
- see_also [[bm-man(3)]]
