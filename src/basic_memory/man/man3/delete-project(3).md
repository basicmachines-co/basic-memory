---
title: delete-project(3)
type: manpage
section: 3
name: delete-project
summary: remove a project (local files survive by default; cloud files are always deleted)
generated: hand
tool: delete_project
verified: 0.21.6 mcp
---

# delete-project(3)

## RETIRED MCP TOOL

`delete_project` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_project_delete` instead;
[[bm-project-delete(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm project remove NAME
```

## SEE ALSO

- see_also [[bm-project-delete(3)]]
