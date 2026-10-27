---
title: create-memory-project(3)
type: manpage
section: 3
name: create-memory-project
summary: create a new project, locally or in a cloud workspace
generated: hand
tool: create_memory_project
verified: 0.21.6 mcp+cli
---

# create-memory-project(3)

## RETIRED MCP TOOL

`create_memory_project` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_project(command="create")` instead;
[[bm-project(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm project add NAME [PATH] [--cloud] [--workspace SELECTOR]
               [--visibility shared|private] [--local-path PATH]
```

## SEE ALSO

- see_also [[bm-project(3)]]
