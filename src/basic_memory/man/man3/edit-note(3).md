---
title: edit-note(3)
type: manpage
section: 3
name: edit-note
summary: 'edit a note in place: append, prepend, find/replace, or section surgery'
generated: hand
tool: edit_note
verified: 0.21.6 mcp+cli
---

# edit-note(3)

## RETIRED MCP TOOL

`edit_note` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_edit` instead;
[[bm-edit(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool edit IDENTIFIER --operation OP --content TEXT
                  [--find-text TEXT] [--section "## Heading"]
                  [--expected-replacements N] [--project NAME]
```

## SEE ALSO

- see_also [[bm-edit(3)]]
