---
title: search-notes(3)
type: manpage
section: 3
name: search-notes
summary: search the knowledge base by text, similarity, or metadata
generated: hand
tool: search_notes
verified: 0.21.6 mcp+cli
---

# search-notes(3)

## RETIRED MCP TOOL

`search_notes` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_search` instead;
[[bm-search(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool search [QUERY] [--project NAME]
                     [--title | --permalink | --vector | --hybrid]
                     [--page N] [--page-size N] [--local | --cloud] ...
```

## SEE ALSO

- see_also [[bm-search(3)]]
