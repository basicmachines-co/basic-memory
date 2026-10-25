---
title: bm-project(3)
type: manpage
section: 3
name: bm-project
summary: list, create, and show recent activity in projects
generated: registry
tool: bm_project
verified: 0.24.1 mcp
---

# bm-project(3)

## NAME

**bm-project** — list, create, and show recent activity in projects

## SYNOPSIS

```
bm_project(command, name=None, path=None, set_default=False, workspace=None,
           since="7d", items=None, page=1, page_size=10,
           output_format="text", project=None)
```

## DESCRIPTION

`command` picks what to do:

- `list` — the projects you can address, grouped by workspace.
- `create` — add a project: `name`, `path`, optional `set_default` and
  `workspace`.
- `recent` — what changed lately: `since`, `items`, paging. `project` picks the
  project, else the default project; with no default, every project. JSON
  output carries `page`, `page_size` and `has_more`, plus `guidance` for any
  project that was never indexed.

bm_project never deletes; bm_project_delete does, and only when the server
enables it.

While the local server is running, its watcher picks up a new project's folder
at the next project reload. Once the watch is attached, a one-time index pass
includes files copied in before attachment; later changes use normal file events.

The shell counterpart for `recent` is tail(1).

## PARAMETERS

- **command** (required) — What to do.
- **name** (string | null, optional, default: None) — For create: the new project's name.
- **path** (string | null, optional, default: None) — For create: the folder that holds the project's notes.
- **set_default** (boolean, optional, default: False) — For create: make the new project the default.
- **workspace** (string | null, optional, default: None) — In Cloud: the workspace slug, name or id that holds the project.
- **since** (string, optional, default: "7d") — How far back to look: '7d', '24h', '2 days ago', 'last week', or a date. Windows shorter than one day are widened to one day.
- **items** (array | null, optional, default: None) — What to return: notes, observations, relations, or a mix. Default: notes.
- **page** (integer, optional, default: 1) — Page number.
- **page_size** (integer, optional, default: 10) — Results per page (1-100).
- **output_format** (optional, default: "text")
- **project** (string | null, optional, default: None) — Project name, 'workspace/project', or project external id. With multiple addressable projects, scope note and search calls explicitly with this parameter or a project-qualified target.

## EXAMPLES

```
bm_project(command="create", name="demo", path="/Users/me/demo", set_default=True)
# → ✓ Project 'demo' added successfully
#   ...
#   Project is now available for use in tool calls; indexing completed.

bm_project(command="list")
# → Available projects:
#   - demo (local) [b2a21302…]
#   - main (local) [9b4d5944…]

bm_project(command="recent", since="1d")
# → ## Recent Activity: demo (1d)
#   **📄 Recent Notes & Documents (1):**
#     • launch-plan (plans) [id: 686e696d…]
```

## SEE ALSO

- see_also [[tail(1)]]
- see_also [[bm-project-delete(3)]]
- see_also [[bm-list(3)]]
