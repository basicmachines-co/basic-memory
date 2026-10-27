---
title: list-workspaces(3)
type: manpage
section: 3
name: list-workspaces
summary: list cloud workspaces available to the authenticated user
generated: hand
tool: list_workspaces
verified: 0.21.6 mcp+cli
---

# list-workspaces(3)

## RETIRED MCP TOOL

`list_workspaces` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_project(command="list")` to discover
projects and their workspace metadata; [[bm-project(3)]] documents its parameters.
This omits accessible workspaces with no projects. Use `bm tool workspaces`
when the CLI is available to discover those workspaces and their slugs before
creating a project.

## CLI

The shell CLI retains its own interface:

```
bm tool workspaces
```

## SEE ALSO

- see_also [[bm-project(3)]]
