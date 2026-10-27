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

## RETIRED MCP TOOL

`list_memory_projects` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_project(command="list")` instead;
[[bm-project(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool projects
bm project list        # richer table, includes routing and sync columns
```

## SEE ALSO

- see_also [[bm-project(3)]]
