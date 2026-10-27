---
title: write-note(3)
type: manpage
section: 3
name: write-note
summary: create or overwrite a markdown note in the knowledge base
generated: hand
tool: write_note
verified: 0.21.6 mcp+cli
---

# write-note(3)

## RETIRED MCP TOOL

`write_note` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_write` instead;
[[bm-write(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool write --title TITLE --folder FOLDER [--content TEXT | < stdin]
                   [--tags TAG] [--type TYPE] [--project NAME | --project-id UUID]
                   [--overwrite [--expected-checksum CHECKSUM]] [--local | --cloud]
```

## SEE ALSO

- see_also [[bm-write(3)]]
