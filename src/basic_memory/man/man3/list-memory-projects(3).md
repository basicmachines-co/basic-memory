---
title: list-memory-projects(3)
type: manpage
section: 3
name: list-memory-projects
summary: list all projects across local config and cloud workspaces
generated: hand
tool: list_memory_projects
verified: 0.21.6 mcp+cli
---

# list-memory-projects(3)

## NAME

**list-memory-projects** — list all projects across local config and cloud workspaces

## SYNOPSIS

MCP:

```
list_memory_projects(output_format="text")
```

CLI:

```
bm tool list-projects
bm project list        # richer table, includes routing and sync columns
```

## DESCRIPTION

Returns a unified view of every reachable project: local projects from
config, plus cloud projects from every workspace the authenticated user can
see, merged by permalink. Each entry carries an `external_id` (UUID) — the
unambiguous handle to pass as `project_id` to other tools when the same
project name exists in more than one workspace.

JSON entries include `qualified_name` (`workspace-slug/project`), `source`
(`local`, `cloud`, `local+cloud`), `cloud_path`, `local_path`, workspace
metadata, and sync capability flags.

## MCP USAGE

```
list_memory_projects(output_format="json")
# → {"projects": [{"name": "manual",
#                  "external_id": "0e6a327b-...",
#                  "qualified_name": "<workspace-slug>/manual",
#                  "source": "cloud", ...}, ...],
#    "default_project": "main"}
```

## CLI EQUIVALENT

```
bm tool list-projects   # same JSON payload
```

## GOTCHAS

- [gotcha] A bare project name that exists in multiple workspaces resolves to the default workspace; use qualified_name or external_id to disambiguate #routing
- [pattern] Discover once, then route by project_id — names are for humans, UUIDs are for tools #routing

## SEE ALSO

- see_also [[create-memory-project(3)]]
- see_also [[delete-project(3)]]
- see_also [[list-workspaces(3)]]
