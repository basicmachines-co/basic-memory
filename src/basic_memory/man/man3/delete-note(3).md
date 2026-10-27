---
title: delete-note(3)
type: manpage
section: 3
name: delete-note
summary: delete a note or directory from the knowledge base
generated: hand
tool: delete_note
verified: 0.21.6 mcp+cli
---

# delete-note(3)

## RETIRED MCP TOOL

`delete_note` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_delete` instead;
[[bm-delete(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool delete IDENTIFIER [--is-directory] [--project NAME]
```

## SEE ALSO

- see_also [[bm-delete(3)]]
