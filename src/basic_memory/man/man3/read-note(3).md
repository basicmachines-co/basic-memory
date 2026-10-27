---
title: read-note(3)
type: manpage
section: 3
name: read-note
summary: read a note by title, permalink, or memory:// URL
generated: hand
tool: read_note
verified: 0.21.6 mcp+cli
---

# read-note(3)

## RETIRED MCP TOOL

`read_note` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_read` instead;
[[bm-read(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool read IDENTIFIER [--project NAME | --project-id UUID]
                  [--start-line N] [--end-line N] [--frontmatter]
                  [--local | --cloud]
```

## SEE ALSO

- see_also [[bm-read(3)]]
