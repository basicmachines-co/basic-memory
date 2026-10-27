---
title: build-context(3)
type: manpage
section: 3
name: build-context
summary: traverse the knowledge graph outward from a memory:// URL
generated: hand
tool: build_context
verified: 0.21.6 mcp
---

# build-context(3)

## RETIRED MCP TOOL

`build_context` was removed from the MCP surface in 1.0. It is not registered
and has no compatibility alias. Use `bm_context` instead;
[[bm-context(3)]] documents its current parameters and examples.

## CLI

The shell CLI retains its own interface:

```
bm tool context URL [--project NAME] [--depth N] [--timeframe SPEC]
                      [--max-related N]
```

## SEE ALSO

- see_also [[bm-context(3)]]
